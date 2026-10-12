"""One Waitress process, with graceful iPerf shutdown on container stop."""

import signal

from waitress import create_server

from . import create_app


def main():
    app = create_app()
    manager = app.extensions["iperf_runs"]
    server = create_server(app, host="0.0.0.0", port=5000, threads=8)

    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    print("FlowBench listening on container port 5000", flush=True)
    try:
        server.run()
    finally:
        manager.shutdown()
        server.close()


if __name__ == "__main__":
    main()
