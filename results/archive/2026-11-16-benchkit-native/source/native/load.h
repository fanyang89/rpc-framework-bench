#pragma once

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
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

// One thread and one persistent connection per outstanding synchronous call.
// Factory and all connection operations stay on the worker (required by KJ).
template <typename Factory>
int run(const Config& config, Factory factory) {
  std::mutex mutex;
  std::condition_variable changed;
  int ready = 0;
  bool start = false;
  std::atomic<bool> failed{false};
  Clock::time_point begin, finish;
  struct Samples {
    std::vector<double> latency;
    Clock::time_point end;
  };
  std::vector<Samples> samples(config.concurrency);
  std::vector<std::thread> workers;
  for (int index = 0; index < config.concurrency; ++index) {
    workers.emplace_back([&, index] {
      bool announced = false;
      try {
        auto call = factory();
        {
          std::unique_lock<std::mutex> lock(mutex);
          ++ready;
          announced = true;
          changed.notify_all();
          changed.wait(lock, [&] { return start; });
        }
        while (Clock::now() < begin && !failed.load()) call();
        auto& result = samples[index];
        while (Clock::now() < finish && !failed.load()) {
          auto before = Clock::now();
          call();
          auto after = Clock::now();
          result.latency.push_back(std::chrono::duration<double, std::micro>(after-before).count());
        }
        result.end = Clock::now();
      } catch (...) {
        failed.store(true);
        std::lock_guard<std::mutex> lock(mutex);
        if (!announced) ++ready;
        changed.notify_all();
      }
    });
  }
  {
    std::unique_lock<std::mutex> lock(mutex);
    changed.wait(lock, [&] { return ready == config.concurrency; });
    begin = Clock::now() + std::chrono::milliseconds(config.warmup_ms);
    finish = begin + std::chrono::milliseconds(config.duration_ms);
    start = true;
    changed.notify_all();
  }
  for (auto& worker : workers) worker.join();
  if (failed.load()) throw std::runtime_error("RPC setup, deadline or response validation failed");
  std::vector<double> latency;
  auto end = finish;
  for (auto& result : samples) {
    end = std::max(end, result.end);
    latency.insert(latency.end(), result.latency.begin(), result.latency.end());
  }
  if (latency.empty()) throw std::runtime_error("no completed RPCs");
  std::sort(latency.begin(), latency.end());
  auto percentile = [&](double fraction) {
    return latency[static_cast<size_t>(std::ceil(fraction * latency.size())) - 1];
  };
  const double seconds = std::chrono::duration<double>(end - begin).count();
  std::cout << "{\"completed\":" << latency.size() << ",\"errors\":0,\"seconds\":" << seconds
            << ",\"ops_per_second\":" << latency.size()/seconds
            << ",\"p50_us\":" << percentile(.50) << ",\"p95_us\":" << percentile(.95)
            << ",\"p99_us\":" << percentile(.99) << "}\n";
  return 0;
}

inline void validate(size_t actual, int expected) {
  if (actual != static_cast<size_t>(expected)) throw std::runtime_error("wrong response size");
}
}  // namespace load
