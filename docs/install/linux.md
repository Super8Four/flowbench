# Ubuntu native installation

[Installation index](README.md) · [Configuration](configuration.md) · [Maintenance](maintenance.md)

These commands target **Ubuntu 24.04** with Python 3.12. The container build exercises the same iPerf source/checksum and packages on AMD64/ARM64; a standalone native-host install has not been separately verified. Use the [container method](containers.md) for the fully checked package.

Install prerequisites:

```sh
sudo apt-get update
sudo apt-get install -y git python3-venv build-essential ca-certificates curl libssl-dev iputils-tracepath
git clone https://github.com/Super8Four/flowbench.git
cd flowbench
git switch --detach v0.1.0-rc.2
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

FlowBench's managed listener needs the full streaming JSON and duration-limit options in the packaged **iPerf3 3.22**. An older distribution package may lack them. Build the same upstream source used by the container in a fresh directory:

```sh
mkdir -p build/iperf-3.22-source bin
cd build/iperf-3.22-source
curl --fail --location --retry 3 --max-time 120 https://downloads.es.net/pub/iperf/iperf-3.22.tar.gz -o iperf.tar.gz
printf '%s\n' '1c0d0fb02c52626111d6e132db80edfbf27bbaff8bd9245df2a371dcb0b35a92  iperf.tar.gz' | sha256sum -c -
tar -xzf iperf.tar.gz
cd iperf-3.22
./configure --disable-shared --enable-static CFLAGS="-O2" LDFLAGS="-s"
make -j2
install -m755 src/iperf3 ../../../bin/iperf3
cd ../../..
bin/iperf3 --version
```

This installs the engine inside the checkout, not a system service. Use a fresh build directory when repeating it. Other distributions need their own package names and are outside these checked instructions.

Set an absolute writable `RUNS_DIRECTORY` in `.env` if desired, then start on loopback:

```sh
.venv/bin/waitress-serve --listen=127.0.0.1:5000 --threads=8 wsgi:app
```

Open **http://127.0.0.1:5000** and check `curl --fail http://127.0.0.1:5000/api/ready` from another terminal. Stop with **Ctrl+C**. Run one app process per archive without a debugger/reloader. No systemd unit, firewall or TLS setup is installed by these commands.
