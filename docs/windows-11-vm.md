# Windows 11 VM Setup

This guide runs the lab inside a Windows 11 VM using Docker Desktop and WSL2.

Architecture:

```text
Windows 11 VM
  Docker Desktop: Docker engine and containers
  Ubuntu on WSL2: bash, git, make, Python, and repo scripts
```

Do not install Docker Engine, `docker.io`, Snap Docker, Podman, or another
container runtime inside Ubuntu for this path. The `docker` command inside
Ubuntu must come from Docker Desktop's WSL integration and talk to Docker
Desktop's Windows engine.

## 1. Install WSL and Docker Desktop

Enable nested virtualization in the outer VM settings first.

Run this in an Administrator PowerShell on Windows:

```powershell
wsl --install --no-distribution
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
shutdown /r /t 0
```

After the reboot, open PowerShell again:

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
Start-Process "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
```

Let Ubuntu create its Linux user. Let Docker Desktop finish starting.

## 2. Enable Docker Desktop WSL Integration

In Docker Desktop on Windows:

1. Open **Settings -> General**.
2. Enable **Use the WSL 2 based engine**.
3. Open **Settings -> Resources -> WSL Integration**.
4. Enable integration for `Ubuntu-24.04`.
5. Click **Apply & Restart**.

Then restart WSL from Windows PowerShell:

```powershell
wsl --shutdown
```

Reopen Ubuntu after this.

## 3. Verify WSL Version

Run this in Windows PowerShell, not inside Ubuntu:

```powershell
wsl -l -v
```

The Ubuntu distro must show `VERSION 2`. If it shows `1`, run:

```powershell
wsl --set-version Ubuntu-24.04 2
wsl --set-default-version 2
```

Use the exact distro name shown by `wsl -l -v`.

If `wsl` is not found, check that you are in Windows PowerShell. It is not an
Ubuntu shell command. From PowerShell, this explicit path should also work:

```powershell
C:\Windows\System32\wsl.exe -l -v
```

## 4. Install Repo Tooling in Ubuntu

Run this inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git make curl ca-certificates python3 python3.12-venv python3-venv python3-pip openssl
```

Verify the repo tools:

```bash
python3 --version
python3 -m venv --help
make --version
```

## 5. Verify Docker Desktop From Ubuntu

Run this inside Ubuntu:

```bash
command -v docker
docker version
docker compose version
docker run --rm hello-world
```

If `docker` is not found, Docker Desktop is not exposed to that Ubuntu distro.
Fix Docker Desktop's WSL integration for `Ubuntu-24.04`, apply/restart Docker
Desktop, run `wsl --shutdown` from Windows PowerShell, and reopen Ubuntu.

Do not fix this by installing Docker inside Ubuntu.

## 6. Clone and Run the Lab

Clone inside the Linux home directory, not under `/mnt/c`:

```bash
cd ~
git clone https://github.com/infinityabundance/developer-support-troubleshooting-lab.git
cd developer-support-troubleshooting-lab
```

If the destination directory already exists, do not clone over it. If it is a
valid checkout, update it:

```bash
cd ~/developer-support-troubleshooting-lab
git pull --ff-only
```

If it is an incomplete directory and `git pull` reports `not a git repository`,
remove it from `~` and clone again:

```bash
cd ~
rm -rf developer-support-troubleshooting-lab
git clone https://github.com/infinityabundance/developer-support-troubleshooting-lab.git
cd developer-support-troubleshooting-lab
```

Create the Python environment and install test dependencies:

```bash
rm -rf .venv   # safe recovery if a previous venv creation failed
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r tests/requirements.txt
python3 -m pytest --version
```

Run Docker and Make commands as the normal Ubuntu user, not with `sudo`. If
Docker reports permission denied for `/var/run/docker.sock`, add the Ubuntu user
to the `docker` group and restart WSL:

```bash
sudo groupadd -f docker
sudo usermod -aG docker "$USER"
```

Then run this from Windows PowerShell and reopen Ubuntu:

```powershell
wsl --shutdown
```

Confirm that `groups` includes `docker`, then run the lab:

```bash
groups

source .venv/bin/activate
make down || true
make up && make reproduce-all && python3 -m pytest -q
```

If the run fails, capture the Compose state before changing anything:

```bash
docker compose ps
docker compose logs --tail=200
```

## Troubleshooting

`python: command not found`

Use `python3`, as shown above. If venv creation fails with `ensurepip is not
available`, install the venv package:

```bash
sudo apt install -y python3.12-venv python3-venv python3-pip
rm -rf .venv
python3 -m venv .venv
```

`.venv/bin/activate: No such file or directory`

The virtual environment does not exist, usually because the earlier venv
creation failed. Install the venv/pip packages, recreate `.venv`, and reinstall
the test dependencies:

```bash
cd ~/developer-support-troubleshooting-lab

sudo apt update
sudo apt install -y python3.12-venv python3-venv python3-pip

rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install --upgrade pip
python3 -m pip install -r tests/requirements.txt
```

`/usr/bin/python3: No module named pip`

The system Python does not have pip installed, or the venv was not created
successfully. Install the Ubuntu pip/venv packages and recreate the venv:

```bash
cd ~/developer-support-troubleshooting-lab

sudo apt update
sudo apt install -y python3.12-venv python3-venv python3-pip

rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install --upgrade pip
python3 -m pip install -r tests/requirements.txt
```

Do not use `sudo apt get pip`; `apt` does not have a `get` operation. Use
`sudo apt install ...`.

`/usr/bin/python3: No module named pytest`

The Docker reproductions already ran; this means the active Python environment
does not have the test dependencies installed. Activate the venv, install the
requirements, then run pytest:

```bash
cd ~/developer-support-troubleshooting-lab

source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r tests/requirements.txt
python3 -m pytest -q
```

If `source .venv/bin/activate` fails, use the `.venv/bin/activate` recovery
steps above first.

`make: command not found`

Install the repo tooling package set:

```bash
sudo apt install -y git make curl ca-certificates python3 python3.12-venv python3-venv python3-pip openssl
```

`docker: command not found`

Do not install Docker inside Ubuntu. Enable Docker Desktop WSL integration for
the Ubuntu distro, apply/restart Docker Desktop, run this from Windows
PowerShell, and reopen Ubuntu:

```powershell
wsl --shutdown
```

`permission denied while trying to connect to the Docker API at unix:///var/run/docker.sock`

Do not run `sudo make`. Running only part of the sequence with `sudo` can make
`make up` work and then make `make reproduce-all` fail as the normal user. Fix
the Ubuntu user's Docker socket access instead:

```bash
sudo groupadd -f docker
sudo usermod -aG docker "$USER"
```

Then run this from Windows PowerShell and reopen Ubuntu:

```powershell
wsl --shutdown
```

Verify and rerun without `sudo`:

```bash
groups
docker version
docker compose ps

cd ~/developer-support-troubleshooting-lab
make down || true
make up && make reproduce-all && python3 -m pytest -q
```

`failed to solve: error getting credentials`

If this appears while pulling a public base image such as `python:3.12-slim`,
Docker can be reading a broken credential-helper config. Reset the Ubuntu-side
Docker client config and retry the pull:

```bash
cd ~
mkdir -p ~/.docker
mv ~/.docker/config.json ~/.docker/config.json.bak 2>/dev/null || true
printf '{}\n' > ~/.docker/config.json

cd ~/developer-support-troubleshooting-lab
docker pull python:3.12-slim
make down || true
make up && make reproduce-all && python3 -m pytest -q
```
