#include "load.h"
#include "Benchmark.h"
#include <thrift/protocol/TBinaryProtocol.h>
#include <thrift/server/TNonblockingServer.h>
#include <thrift/transport/TNonblockingServerSocket.h>
#include <thrift/transport/TSocket.h>
#include <thrift/transport/TBufferTransports.h>

using namespace apache::thrift;

class Service final : public native_rpc::BenchmarkIf {
  void call(std::string& result, const std::string&, int32_t size) override {
    if (size < 0 || size > 1048576) throw TException("invalid response size");
    result.assign(size, '\0');
  }
};

int main(int argc, char** argv) {
  try {
    if (argc == 4 && std::string(argv[1]) == "server") {
      auto processor = std::make_shared<native_rpc::BenchmarkProcessor>(std::make_shared<Service>());
      auto socket = std::make_shared<transport::TNonblockingServerSocket>(
          "127.0.0.1", load::number(argv[2], 1024, 65535));
      server::TNonblockingServer server(processor,
          std::make_shared<protocol::TBinaryProtocolFactory>(), socket);
      server.setNumIOThreads(load::number(argv[3], 1, 8));
      server.serve();
      return 0;
    }
    auto config = load::parse(argc, argv);
    return load::run(config, [&] {
      auto separator = config.address.rfind(':');
      if (separator == std::string::npos) throw std::invalid_argument("missing port");
      auto socket = std::make_shared<transport::TSocket>(config.address.substr(0, separator),
          load::number(config.address.substr(separator + 1).c_str(), 1024, 65535));
      socket->setConnTimeout(10000);
      socket->setRecvTimeout(10000);
      socket->setSendTimeout(10000);
      socket->setNoDelay(true);
      auto transport = std::make_shared<transport::TFramedTransport>(socket);
      auto client = std::make_shared<native_rpc::BenchmarkClient>(
          std::make_shared<protocol::TBinaryProtocol>(transport));
      transport->open();
      return [client, config, request = std::string(config.request_size, '\0'),
              response = std::string()]() mutable {
        response.clear();
        client->call(response, request, config.response_size);
        load::validate(response.size(), config.response_size);
      };
    });
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
