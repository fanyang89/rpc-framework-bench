#include "load.h"
#include <cassert>
#include <cmath>

int main() {
  for (const char* input : {"-1", "257", "1x"}) {
    bool rejected = false;
    try { load::number(input, 0, 256); }
    catch (const std::exception&) { rejected = true; }
    assert(rejected);
  }
  bool rejected = false;
  try { load::validate(3, 4); }
  catch (const std::exception&) { rejected = true; }
  assert(rejected);

  load::Histogram hist;
  for (uint64_t v : {1000u, 2000u, 3000u, 4000u}) hist.Record(v);
  assert(hist.PercentileUs(0.50) == 2.0);
  assert(hist.PercentileUs(0.99) == 4.0);
  assert(hist.min_us() == 1.0 && hist.max_us() == 4.0 && hist.mean_us() == 2.5);

  load::Histogram wide;
  for (uint64_t v : {100u, 512u, 15500u, 125000u, 1500000u, 13500000u}) wide.Record(v);
  assert(std::abs(wide.PercentileUs(0.50) - 15.5) < 0.1);
  assert(std::abs(wide.PercentileUs(0.99) - 13500.0) < 60.0);

  load::Config config{"unused", 4, 0, 0, 1, 5};
  for (bool fail_setup : {false, true}) {
    rejected = false;
    try {
      load::run(config, [fail_setup] {
        if (fail_setup) throw std::runtime_error("setup failure");
        return [] { throw std::runtime_error("RPC failure"); };
      });
    } catch (const std::exception&) { rejected = true; }
    assert(rejected);
  }
  std::atomic<int> calls{0};
  int status = load::run(config, [&] {
    return [&] { ++calls; std::this_thread::yield(); };
  });
  assert(status == 0 && calls > 0);
}
