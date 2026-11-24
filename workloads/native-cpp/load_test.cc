#include "load.h"
#include <cassert>

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
