"""Scheduler setup reference.

This module is not executed — it's a reference for setting up
scheduled execution via cron (Linux/WSL) or Task Scheduler (Windows).
"""

CRON_EXAMPLE = """
# Linux/WSL cron setup
# Edit crontab: crontab -e
# Run every 4 hours (adjust as needed):
0 */4 * * * cd /path/to/ma-signal-monitor && /path/to/.venv/bin/python -m ma_signal_monitor.main >> /path/to/ma-signal-monitor/logs/cron.log 2>&1

# Run daily at 7am:
0 7 * * * cd /path/to/ma-signal-monitor && /path/to/.venv/bin/python -m ma_signal_monitor.main >> /path/to/ma-signal-monitor/logs/cron.log 2>&1

# Verify cron is running:
# crontab -l
# grep CRON /var/log/syslog
"""

WINDOWS_TASK_SCHEDULER = """
# Windows Task Scheduler setup

1. Open Task Scheduler (taskschd.msc)
2. Create Basic Task:
   - Name: "MA Signal Monitor"
   - Trigger: Daily (or desired schedule)
   - Action: Start a program
   - Program: C:\\path\\to\\.venv\\Scripts\\python.exe
   - Arguments: -m ma_signal_monitor.main
   - Start in: C:\\path\\to\\ma-signal-monitor

Alternative using PowerShell script (scripts/run_once.ps1):

$ErrorActionPreference = "Stop"
Set-Location "C:\\path\\to\\ma-signal-monitor"
& .venv\\Scripts\\python.exe -m ma_signal_monitor.main 2>&1 |
    Out-File -Append logs\\scheduler.log
"""

SCRIPTS_RUN_ONCE_NOTE = """
For one-off execution or testing, use:
    python scripts/run_once.py

Or directly:
    python -m ma_signal_monitor.main
"""
