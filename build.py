#!/usr/bin/env python3
"""Build an installable user-local bundle. Run with --install to install it."""
import argparse
import compileall
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import gi

from PIL import Image

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--install', action='store_true')
args = parser.parse_args()
# Validate dependencies before installing anything.
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gtk, Vte

stage = ROOT / 'dist' / 'nocturne-1.0.0'
stage.mkdir(parents=True, exist_ok=True)
for name in ('nocturne.py', 'README.md'):
    shutil.copy2(ROOT / name, stage / name)
shutil.copytree(ROOT / 'assets', stage / 'assets', dirs_exist_ok=True)
assert compileall.compile_file(str(stage / 'nocturne.py'), quiet=1)
icons = stage / 'icons'
source = Image.open(stage / 'assets' / 'nocturne-icon.png').convert('RGBA')
for size in (16, 24, 32, 48, 64, 128, 256, 512):
    target = icons / f'{size}x{size}' / 'apps' / 'io.nocturne.Terminal.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    scaled = source.resize((size, size), Image.Resampling.LANCZOS)
    scaled.save(target)
archive = ROOT / 'dist' / 'nocturne-1.0.0-linux.tar.gz'
with tarfile.open(archive, 'w:gz') as bundle:
    bundle.add(stage, arcname=stage.name, filter=lambda info: None if '__pycache__' in info.name else info)
print(f'Built {archive}')

if args.install:
    # The official OpenRouter agent SDK stays isolated from GTK and from the LiteLLM gateway.
    import nocturne as n
    runtime = n.agent_runtime()
    if not (runtime / 'bin/python').is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(runtime)], check=True)
    subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', *n.AGENT_SDK_PACKAGES], check=True)
    subprocess.run([str(runtime / 'bin/python'), '-c', 'import openrouter_agent'], check=True)
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    destination = data / 'nocturne'
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('nocturne.py', 'README.md'):
        shutil.copy2(stage / name, destination / name)
    shutil.copytree(stage / 'assets', destination / 'assets', dirs_exist_ok=True)
    shutil.copytree(icons, data / 'icons/hicolor', dirs_exist_ok=True)
    assert compileall.compile_file(str(destination / 'nocturne.py'), quiet=1)
    executable = Path.home() / '.local/bin/nocturne'
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('#!/bin/sh\nnocturne_log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/nocturne"\nmkdir -p "$nocturne_log_dir"\nif [ -t 2 ]; then\n    exec /usr/bin/python3 -u ' + shlex.quote(str(destination / 'nocturne.py')) + ' "$@"\nfi\nexec /usr/bin/python3 -u ' + shlex.quote(str(destination / 'nocturne.py')) + ' "$@" 2>>"$nocturne_log_dir/startup.log"\n')
    executable.chmod(0o755)
    agent_executable = executable.with_name('nocturne-agent')
    agent_executable.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(destination / 'nocturne.py')) + ' --agent-cli "$@"\n')
    agent_executable.chmod(0o755)
    desktop = data / 'applications/io.nocturne.Terminal.desktop'
    desktop.parent.mkdir(parents=True, exist_ok=True)
    exec_path = str(executable).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Nocturne Terminal
Comment=Project terminals for Claude Code and Codex
Exec="{exec_path}"
TryExec={executable}
Icon=io.nocturne.Terminal
Terminal=false
Categories=System;TerminalEmulator;
StartupNotify=true
StartupWMClass=io.nocturne.Terminal
''')
    for command in (["gtk-update-icon-cache", '-f', '-t', str(data / 'icons/hicolor')], ['update-desktop-database', str(desktop.parent)], ['desktop-file-validate', str(desktop)]):
        if shutil.which(command[0]):
            subprocess.run(command, check=True)
    print(f'Installed application: {destination}')
    print(f'Installed launcher: {desktop}')
    print(f'Launch: {executable}')
    print(f'Agent CLI: {agent_executable}')
