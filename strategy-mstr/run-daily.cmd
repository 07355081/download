@echo off
rem 手动补跑用，不要再注册成计划任务：日更已迁到 VPS crontab 07:00
rem （/root/data-download/run_strategy_mstr_daily.sh）。两边同时跑会抢写线上同一份
rem public/json/strategy-mstr，且本机历史一旦落后就会把 VPS 的覆盖回去。
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
python daily.py --push-vps >> logs\daily.log 2>&1
