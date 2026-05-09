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

rm -rf .venv   # safe recovery if a previous venv creation failed
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r tests/requirements.txt

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
