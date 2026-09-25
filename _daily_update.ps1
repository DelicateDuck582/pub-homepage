<#
  _daily_update.ps1 —— 每天从 GitHub 取一次提交日志，更新分支拓扑图与提交时间线的整理

  用法：
    powershell -NoProfile -ExecutionPolicy Bypass -File _daily_update.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File _daily_update.ps1 -Deploy
    powershell -NoProfile -ExecutionPolicy Bypass -File _daily_update.ps1 -Publish -Api https://worklog-collector.xxx.workers.dev -PublishToken 口令

  流程：
    1) 对 cloud-mail.git / SPlayer.git 执行 git fetch --all --prune（只拉提交图，不拉文件内容）
    2) 跑 _collect.py 重新采集（含真实父子分支关系、提交类型判定）
    2.5) 带 -Api 时顺路让 Worker 逐条判定提交类型，并取回 worklog-kinds.json（取不到就忽略，页面照旧）
    3) 采集结果有变化才跑 _gen_worklog.py + _pack_site.py 重新生成页面与 dist.zip
    4) 带 -Deploy 时调用 deploy-site.ps1 推到线上（Cloudflare Pages 项目 www-32321）
    5) 带 -Publish 时把 worklog.html 与数据推给 Worker（页面打开时会拉取，自动换成最新渲染结果）
       口令也可以放环境变量：WORKLOG_API、WORKLOG_PUBLISH_TOKEN

  注册为每天 21:30 自动执行（只更新本地）：
    schtasks /Create /TN "worklog-daily-update" /SC DAILY /ST 21:30 /F /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File _daily_update.ps1"
  想连部署和发布一起做，就把 -TR 里换成 -File 后面的参数：-Deploy -Publish -Api https://… -PublishToken 口令
  取消自动执行：
    schtasks /Delete /TN "worklog-daily-update" /F
  手动跑一次：
    schtasks /Run /TN "worklog-daily-update"
#>
param([switch]$Deploy, [switch]$Publish, [string]$Api = '', [string]$PublishToken = '', [string]$SiteDir = '')

$ErrorActionPreference = 'Stop'
$WORK = Split-Path -Parent $MyInvocation.MyCommand.Path
$SITE = if ($SiteDir) { $SiteDir } else { $WORK }
$PACKER = ''      # 可选：自己写一个把页面打包成 dist.zip 的脚本，填路径即可
$DEPLOY_SCRIPT = ''   # 可选：自己的 Cloudflare Pages 部署脚本
$LOG = Join-Path $WORK '_daily_update.log'
if (-not $Api) { $Api = $env:WORKLOG_API }
if (-not $PublishToken) { $PublishToken = $env:WORKLOG_PUBLISH_TOKEN }

function Write-Log([string]$msg) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Write-Host $line
  Add-Content -Path $LOG -Value $line -Encoding UTF8
}

function Get-PythonPath {
  foreach ($c in @('python.exe', 'python3.exe', 'py.exe')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  throw '找不到 python，请确认已安装并加入 PATH'
}

if ((Test-Path $LOG) -and ((Get-Item $LOG).Length -gt 512KB)) { Move-Item $LOG "$LOG.1" -Force }
Write-Log '=== 工作日志每日更新：开始 ==='

try {
  $PY = Get-PythonPath

  # 1) 拉取两个源仓库的最新提交图
  foreach ($bare in @('cloud-mail.git', 'SPlayer.git')) {
    $repo = Join-Path $WORK $bare
    if (-not (Test-Path $repo)) { Write-Log "跳过 $bare（目录不存在，需先 --bare --filter=blob:none 克隆）"; continue }
    git -C $repo fetch --all --prune --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Log "警告：$bare fetch 失败（网络/代理？），本次沿用本地已有数据" }
    else { Write-Log "已拉取 $bare" }
  }

  # 2) 重新采集
  $data = Join-Path $WORK 'worklog-data.json'
  $before = if (Test-Path $data) { (Get-FileHash $data -Algorithm SHA1).Hash } else { '' }
  Push-Location $WORK
  try { & $PY '_collect.py' *> (Join-Path $WORK '_collect.log') } finally { Pop-Location }
  if ($LASTEXITCODE -ne 0) { throw '采集失败，详见 _collect.log' }
  $after = (Get-FileHash $data -Algorithm SHA1).Hash
  Write-Log ('采集完成：' + ((Get-Content (Join-Path $WORK '_collect.log') -Tail 1) -join ''))

  # 2.5) 有 -Api 时：先把新数据推给 Worker，再让它给新提交判类型（在既有 13 个类型里选），最后把结果取回来
  #      （顺序很重要：判定是按 Worker 里那份数据做的，不先推数据就会判成上一版）
  if ($Api) {
    $api = $Api.TrimEnd('/')
    $kindFile = Join-Path $WORK 'worklog-kinds.json'
    $head = @{ Authorization = "Bearer $PublishToken" }
    try {
      if ($PublishToken) {
        Invoke-RestMethod -Method Put -Uri "$api/publish/data.json" -Headers $head `
          -ContentType 'application/json; charset=utf-8' -InFile $data | Out-Null
        # 每天只做几批（每批 25 条），够补完当天新增的提交；第一次全量要连着多跑几轮
        $r = Invoke-RestMethod -Method Post -Uri "$api/classify?batches=6" -Headers $head
        Write-Log ('已把数据推给 Worker，并让 AI 判了类型：本次 ' + $r.classified + '/' + $r.total + '，还差 ' + $r.pending)
      }
      Invoke-RestMethod -Uri "$api/classify.json" -OutFile $kindFile
      Write-Log ('已取回 AI 类型判定（' + (Get-Item $kindFile).Length + ' 字节）')
    } catch {
      Write-Log ('取/写 AI 类型判定失败（忽略，页面照旧）：' + $_.Exception.Message)
    }
  }

  if ($before -eq $after) {
    Write-Log '提交图没有变化，页面保持原样（跳过重新生成）'
  } else {
    # 3) 重新生成页面 + 重新打包
    Push-Location $WORK
    try { & $PY '_gen_worklog.py' *> (Join-Path $WORK '_gen.log') } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw '生成失败，详见 _gen.log' }
    Write-Log ('页面已更新：' + ((Get-Content (Join-Path $WORK '_gen.log') -Tail 1) -join ''))

    if ($PACKER -and (Test-Path $PACKER)) { Push-Location (Split-Path -Parent $PACKER)
      try { & $PY $PACKER *> (Join-Path $WORK '_pack.log') } finally { Pop-Location } }
    if ($LASTEXITCODE -ne 0) { Write-Log '打包失败，详见 _pack.log' }
    else { Write-Log ('已重新打包：' + ((Get-Content (Join-Path $WORK '_pack.log') -Last 1) -join '')) }
  }

  # 4) 可选：推送上线（注意变量名不能与 -Deploy 开关重名）
  if ($Deploy) {
    if (Test-Path $DEPLOY_SCRIPT) {
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $DEPLOY_SCRIPT
      Write-Log ('已调用部署脚本（退出码 ' + $LASTEXITCODE + '）')
    } else { Write-Log "没找到部署脚本 $DEPLOY_SCRIPT，跳过部署" }
  }

  # 5) 可选：把新页面推给 Cloudflare Worker（页面打开时会拉它，自动换成最新渲染结果）
  if ($Publish) {
    if (-not $Api -or -not $PublishToken) {
      Write-Log '要发布到 Worker，需要 -Api https://xxx.workers.dev 与 -PublishToken（或用环境变量 WORKLOG_API / WORKLOG_PUBLISH_TOKEN）'
    } else {
      $api = $Api.TrimEnd('/')
      $head = @{ Authorization = "Bearer $PublishToken" }
      try {
        Invoke-RestMethod -Method Put -Uri "$api/publish/page.html" -Headers $head `
          -ContentType 'text/html; charset=utf-8' -InFile (Join-Path $SITE 'worklog.html') | Out-Null
        Invoke-RestMethod -Method Put -Uri "$api/publish/data.json" -Headers $head `
          -ContentType 'application/json; charset=utf-8' -InFile (Join-Path $WORK 'worklog-data.json') | Out-Null
        Write-Log ('已发布到 Worker：worklog.html ' + (Get-Item (Join-Path $SITE 'worklog.html')).Length + ' 字节')
      } catch {
        Write-Log ('发布到 Worker 失败：' + $_.Exception.Message)
      }
    }
  }

  Write-Log '=== 结束（成功） ==='
  exit 0
} catch {
  Write-Log ('=== 结束（失败）：' + $_.Exception.Message + ' ===')
  exit 1
}
