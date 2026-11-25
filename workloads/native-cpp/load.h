#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace load {
using Clock = std::chrono::steady_clock;

inline int number(const char* text, int minimum, int maximum) {
  std::string input(text);
  size_t end = 0;
  long value = std::stol(input, &end);
  if (end != input.size() || value < minimum || value > maximum)
    throw std::invalid_argument("argument out of range");
  return static_cast<int>(value);
}

struct Config {
  std::string address;
  int concurrency, request_size, response_size, warmup_ms, duration_ms;
};

inline Config parse(int argc, char** argv) {
  if (argc != 8) throw std::invalid_argument(
      "client ADDRESS CONCURRENCY REQUEST_BYTES RESPONSE_BYTES WARMUP_MS DURATION_MS");
  return {argv[2], number(argv[3], 1, 256), number(argv[4], 0, 1048576),
          number(argv[5], 0, 1048576), number(argv[6], 0, 60000),
          number(argv[7], 1, 60000)};
}

// Log-linear histogram with 8-bit mantissa (256 sub-buckets per octave, <0.4% error,
// exact below 512 ns, covers up to ~68.7 s) for O(1) zero-allocation latency recording.
class Histogram {
 public:
  static constexpr int kSubBits = 8;
  static constexpr size_t kSubSize = 1u << kSubBits;
  static constexpr size_t kBuckets = kSubSize * 30;

  Histogram() { buckets_.fill(0); }

  static size_t Bucket(uint64_t ns) {
    if (ns < 2 * kSubSize) return static_cast<size_t>(ns);
    const int msb = 63 - __builtin_clzll(ns);
    const int shift = msb - kSubBits;
    const size_t octave = static_cast<size_t>(shift + 1);
    const size_t sub = static_cast<size_t>((ns >> shift) & (kSubSize - 1));
    const size_t idx = octave * kSubSize + sub;
    return idx < kBuckets ? idx : kBuckets - 1;
  }

  static uint64_t Value(size_t idx) {
    if (idx < 2 * kSubSize) return static_cast<uint64_t>(idx);
    const size_t octave = idx >> kSubBits;
    const uint64_t sub = static_cast<uint64_t>(idx & (kSubSize - 1));
    return (static_cast<uint64_t>(kSubSize) + sub) << (octave - 1);
  }

  void Record(uint64_t ns) {
    ++buckets_[Bucket(ns)];
    if (count_ == 0 || ns < min_ns_) min_ns_ = ns;
    if (ns > max_ns_) max_ns_ = ns;
    ++count_;
    sum_ns_ += ns;
  }

  void Merge(const Histogram& other) {
    if (other.count_ == 0) return;
    if (count_ == 0 || other.min_ns_ < min_ns_) min_ns_ = other.min_ns_;
    if (other.max_ns_ > max_ns_) max_ns_ = other.max_ns_;
    count_ += other.count_;
    sum_ns_ += other.sum_ns_;
    for (size_t i = 0; i < kBuckets; ++i) buckets_[i] += other.buckets_[i];
  }

  double PercentileUs(double fraction) const {
    if (count_ == 0) return 0.0;
    uint64_t rank = static_cast<uint64_t>(std::ceil(fraction * static_cast<double>(count_)));
    if (rank == 0) rank = 1;
    uint64_t seen = 0;
    for (size_t i = 0; i < kBuckets; ++i) {
      seen += buckets_[i];
      if (seen >= rank) {
        uint64_t v = std::clamp(Value(i), min_ns_, max_ns_);
        return static_cast<double>(v) / 1000.0;
      }
    }
    return static_cast<double>(max_ns_) / 1000.0;
  }

  uint64_t count() const { return count_; }
  double min_us() const { return count_ ? static_cast<double>(min_ns_) / 1000.0 : 0.0; }
  double max_us() const { return count_ ? static_cast<double>(max_ns_) / 1000.0 : 0.0; }
  double mean_us() const {
    return count_ ? static_cast<double>(sum_ns_) / static_cast<double>(count_) / 1000.0 : 0.0;
  }

 private:
  std::array<uint64_t, kBuckets> buckets_;
  uint64_t count_ = 0;
  uint64_t min_ns_ = 0;
  uint64_t max_ns_ = 0;
  unsigned __int128 sum_ns_ = 0;
};

// One thread and one persistent connection per outstanding synchronous call.
// Factory and all connection operations stay on the worker (required by KJ).
template <typename Factory>
int run(const Config& config, Factory factory) {
  std::mutex mutex;
  std::condition_variable changed;
  int setup_ready = 0;
  int warmup_ready = 0;
  bool warmup_start = false;
  bool measure_start = false;
  std::atomic<bool> failed{false};
  std::string first_error;
  Clock::time_point warmup_end, measure_begin, measure_end;

  struct WorkerResult {
    Histogram hist;
    double active_seconds = 0.0;
  };
  std::vector<WorkerResult> results(config.concurrency);
  std::vector<std::thread> workers;
  workers.reserve(config.concurrency);

  for (int index = 0; index < config.concurrency; ++index) {
    workers.emplace_back([&, index] {
      bool announced_setup = false;
      bool announced_warmup = false;
      try {
        auto call = factory();
        // Barrier 1: all workers connected before warmup starts.
        {
          std::unique_lock<std::mutex> lock(mutex);
          ++setup_ready;
          announced_setup = true;
          changed.notify_all();
          changed.wait(lock, [&] { return warmup_start || failed.load(); });
        }
        if (failed.load()) return;
        while (Clock::now() < warmup_end && !failed.load()) call();
        // Barrier 2: all workers finished warmup before synchronized measurement begins.
        {
          std::unique_lock<std::mutex> lock(mutex);
          ++warmup_ready;
          announced_warmup = true;
          changed.notify_all();
          changed.wait(lock, [&] { return measure_start || failed.load(); });
        }
        if (failed.load()) return;
        std::this_thread::sleep_until(measure_begin);
        auto& result = results[index];
        auto now = Clock::now();
        while (now < measure_end && !failed.load()) {
          const auto before = now;
          call();
          now = Clock::now();
          const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now - before).count();
          result.hist.Record(ns > 0 ? static_cast<uint64_t>(ns) : 0u);
        }
        result.active_seconds = now > measure_begin
            ? std::chrono::duration<double>(now - measure_begin).count()
            : static_cast<double>(config.duration_ms) / 1000.0;
      } catch (const std::exception& e) {
        failed.store(true);
        std::lock_guard<std::mutex> lock(mutex);
        if (first_error.empty()) first_error = e.what();
        if (!announced_setup) ++setup_ready;
        if (!announced_warmup) ++warmup_ready;
        changed.notify_all();
      } catch (...) {
        failed.store(true);
        std::lock_guard<std::mutex> lock(mutex);
        if (first_error.empty()) first_error = "unknown worker exception";
        if (!announced_setup) ++setup_ready;
        if (!announced_warmup) ++warmup_ready;
        changed.notify_all();
      }
    });
  }

  {
    std::unique_lock<std::mutex> lock(mutex);
    changed.wait(lock, [&] { return setup_ready == config.concurrency; });
    if (!failed.load()) {
      warmup_end = Clock::now() + std::chrono::milliseconds(config.warmup_ms);
      warmup_start = true;
      changed.notify_all();
      changed.wait(lock, [&] { return warmup_ready == config.concurrency; });
      if (!failed.load()) {
        measure_begin = Clock::now() + std::chrono::milliseconds(10);
        measure_end = measure_begin + std::chrono::milliseconds(config.duration_ms);
        measure_start = true;
        changed.notify_all();
      }
    } else {
      warmup_start = true;
      measure_start = true;
      changed.notify_all();
    }
  }

  for (auto& worker : workers) worker.join();
  if (failed.load()) {
    throw std::runtime_error(
        first_error.empty() ? "RPC setup, deadline or response validation failed" : first_error);
  }

  Histogram combined;
  double ops_per_second = 0.0;
  for (const auto& result : results) {
    combined.Merge(result.hist);
    if (result.hist.count() > 0 && result.active_seconds > 0.0) {
      ops_per_second += static_cast<double>(result.hist.count()) / result.active_seconds;
    }
  }
  if (combined.count() == 0) throw std::runtime_error("no completed RPCs");

  const double effective_seconds = static_cast<double>(combined.count()) / ops_per_second;
  std::cout << "{\"completed\":" << combined.count()
            << ",\"errors\":0"
            << ",\"seconds\":" << effective_seconds
            << ",\"ops_per_second\":" << ops_per_second
            << ",\"p50_us\":" << combined.PercentileUs(.50)
            << ",\"p95_us\":" << combined.PercentileUs(.95)
            << ",\"p99_us\":" << combined.PercentileUs(.99)
            << ",\"min_us\":" << combined.min_us()
            << ",\"max_us\":" << combined.max_us()
            << ",\"mean_us\":" << combined.mean_us() << "}\n";
  return 0;
}

inline void validate(size_t actual, int expected) {
  if (actual != static_cast<size_t>(expected)) throw std::runtime_error("wrong response size");
}
}  // namespace load
