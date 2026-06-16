A desktop GUI application for managing SSH sessions and port forwarding tunnels, inspired by MobaXterm.

Built with Python + Tkinter. No external dependencies required beyond the Python standard library.

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

## Features

- **SSH Session Management** — Create, edit, duplicate, and delete SSH connections
- **Port Forwarding** — Configure local (-L), remote (-R), and dynamic (-D) forwarding rules
- **Authentication** — Support for both password and SSH key authentication
- **Keepalive / Anti-Idle** — Configurable keepalive interval to prevent connection timeouts
- **Batch Operations** — Connect / disconnect all sessions at once
- **Persistent Config** — Sessions saved to a local JSON file (~/.ssh_tunnel_manager/sessions.json)

## Requirements

- Python 3.11+
- Windows (tested on Windows 10/11)
- Optional: sshpass for password-based non-interactive SSH authentication

## Installation

### Run from source

`
git clone https://github.com/Doitky/ssh-tunnel-manager.git
cd ssh-tunnel-manager
python ssh_tunnel_manager.py
`

### Build standalone EXE

`
pip install pyinstaller
pyinstaller --onefile --windowed ssh_tunnel_manager.py
# Output: dist/SSH-Tunnel-Manager.exe
`

## Usage

1. Click **+ New Session** to create a connection.
2. Fill in the **General** tab (host, port, username, auth method).
3. Optionally configure **Port Forwarding** rules.
4. Optionally enable **Keepalive** with a custom interval.
5. Click **Save**, then double-click the session or select it and click **Connect**.

## Configuration

Sessions are stored in:

`
%USERPROFILE%\.ssh_tunnel_manager\sessions.json
`

## License

MIT License. See [LICENSE](LICENSE) for details.

## Author

**Doitky**
=======
