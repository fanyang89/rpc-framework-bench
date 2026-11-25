#include "load.h"
#include "benchmark.capnp.h"
#include <capnp/ez-rpc.h>
#include <kj/async-io.h>
#include <kj/debug.h>
#include <kj/timer.h>
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#include <memory>

class Service final : public native_rpc::Benchmark::Server {
  kj::Promise<void> call(CallContext context) override {
    auto size = context.getParams().getResponseSize();
    KJ_REQUIRE(size <= 1048576);
    context.getResults().initPayload(size);
    return kj::READY_NOW;
  }
};

int make_listener(int port) {
  int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  int enabled = 1;
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port);
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (fd < 0 ||
      setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled)) != 0 ||
      setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &enabled, sizeof(enabled)) != 0 ||
      setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) != 0 ||
      bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
      listen(fd, 512) != 0) {
    if (fd >= 0) close(fd);
    throw std::runtime_error("listen failed");
  }
  return fd;
}

void serve(int fd, int port) {
  capnp::EzRpcServer server(kj::heap<Service>(), fd, port);
  kj::NEVER_DONE.wait(server.getWaitScope());
}

int main(int argc, char** argv) {
  try {
    if (argc == 4 && std::string(argv[1]) == "server") {
      int port = load::number(argv[2], 1024, 65535);
      int count = load::number(argv[3], 1, 8);
      // Create and listen on ALL SO_REUSEPORT sockets before spawning worker
      // loops so readiness probes never race ahead of remaining listeners.
      std::vector<int> listeners;
      listeners.reserve(count);
      for (int i = 0; i < count; ++i) listeners.push_back(make_listener(port));
      std::vector<std::thread> threads;
      threads.reserve(count);
      for (int fd : listeners) threads.emplace_back([fd, port] { serve(fd, port); });
      for (auto& thread : threads) thread.join();
      return 0;
    }
    auto config = load::parse(argc, argv);
    return load::run(config, [&] {
      auto connection = std::make_unique<capnp::EzRpcClient>(config.address.c_str());
      auto client = connection->getMain<native_rpc::Benchmark>();
      return [connection = std::move(connection), client = kj::mv(client), config,
              payload = std::string(config.request_size, '\0')]() mutable {
        auto request = client.callRequest();
        request.setPayload(kj::arrayPtr(reinterpret_cast<const kj::byte*>(payload.data()), payload.size()));
        request.setResponseSize(config.response_size);
        auto response = connection->getIoProvider().getTimer()
            .timeoutAfter(10 * kj::SECONDS, request.send())
            .wait(connection->getWaitScope());
        load::validate(response.getPayload().size(), config.response_size);
      };
    });
  } catch (const kj::Exception& error) {
    std::cerr << error.getDescription().cStr() << '\n';
    return 1;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
