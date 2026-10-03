# 注册/更新 Windows 计划任务「DataLake Coverage Check」：每天跑一次数据湖覆盖率/时效性检查。
#
# 用法：
#   powershell -File .\register_coverage_task.ps1
#
# 时间选在本机时间 10:30（VPS 每日任务 09:00 CST 完成下载+派生 parquet 后，
# 留出本机 30 分钟拉取窗口的缓冲）。只写日志/退出码，不产出网页数据、不发通知——
# 要看结果就读 E:\data-pipeline\logs\coverage_YYYYMMDD.log，或用
# `schtasks /query /tn "\DataLake Coverage Check" /fo LIST /v` 看「上次结果」
# （0=正常，1=有 ERROR，需要打开当天日志看细节）。
#
# 幂等：重复运行会用 /F 覆盖同名任务。

$TaskName = "\DataLake Coverage Check"
$ScriptPath = "/mnt/e/Code/data-download/data-lake/check_lake_coverage.py"

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Daily coverage/freshness check for tradfi-price/oi/funding parquet lake. Log-only, exit code 1 on ERROR. Script is git-tracked at data-download/data-lake/check_lake_coverage.py.</Description>
    <URI>$TaskName</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-07-15T10:30:00+08:00</StartBoundary>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d Ubuntu -u root -- python3 $ScriptPath</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$tmpXml = Join-Path $env:TEMP "datalake-coverage-task.xml"
[System.IO.File]::WriteAllText($tmpXml, $xml, [System.Text.Encoding]::Unicode)

schtasks /create /tn $TaskName /xml $tmpXml /f

Remove-Item $tmpXml -ErrorAction SilentlyContinue

Write-Host "已注册/更新任务: $TaskName -> wsl.exe -d Ubuntu -u root -- python3 $ScriptPath"
