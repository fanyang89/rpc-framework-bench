@0xdae5a6a4a93baf11;
using Cxx = import "/capnp/c++.capnp";
$Cxx.namespace("native_rpc");

interface Benchmark {
  call @0 (payload :Data, responseSize :UInt32) -> (payload :Data);
}
