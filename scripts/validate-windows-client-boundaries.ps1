$ErrorActionPreference = "Stop"
$projectPath = Join-Path $PSScriptRoot "../clients/site-manager/UPM.SiteManager.csproj"
[xml]$project = Get-Content $projectPath

if ($project.Project.PropertyGroup.OutputType -notcontains "WinExe") {
    throw "UPM Site Manager must remain its own WinExe application."
}

$references = @($project.Project.ItemGroup.ProjectReference.Include)
$agentReferences = $references | Where-Object { $_ -match "(^|[\\/])agent([\\/]|$)" }
if ($agentReferences) {
    throw "UPM Site Manager must not reference the UPM Agent product: $agentReferences"
}

$siteManagerFiles = Get-ChildItem (Join-Path $PSScriptRoot "../clients/site-manager") -Recurse -File
$forbiddenRuntimeFiles = $siteManagerFiles | Where-Object {
    $_.Name -match "Agent(Service|Companion|Runtime)|PowerPointLauncher|RoomMachineRuntime"
}
if ($forbiddenRuntimeFiles) {
    throw "Agent or room-machine runtime functionality was placed in Site Manager: $($forbiddenRuntimeFiles.FullName)"
}

Write-Host "Validated Site Manager executable and Agent product boundary."

$requiredPages = @(
    "DashboardPage", "IntakePage", "PresentationsPage", "RoomsPage", "TransfersPage",
    "ReviewSessionsPage", "DevicesPage", "ActivityPage", "SettingsPage"
)
foreach ($page in $requiredPages) {
    foreach ($extension in @("xaml", "xaml.cs")) {
        $pagePath = Join-Path $PSScriptRoot "../clients/site-manager/Views/$page.$extension"
        if (-not (Test-Path $pagePath)) {
            throw "Missing native navigation target: $pagePath"
        }
    }
}

$windowCode = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/MainWindow.xaml.cs") -Raw
foreach ($page in $requiredPages) {
    if ($windowCode -notmatch [regex]::Escape("typeof($page)")) {
        throw "MainWindow does not map a navigation item to $page."
    }
}
if ($windowCode -match "_\s*=>\s*typeof\(DashboardPage\)") {
    throw "Unknown navigation targets must not fall back to Dashboard."
}

Write-Host "Validated all nine distinct native navigation targets."

$roomWorkspace = Join-Path $PSScriptRoot "../clients/site-manager/Views/RoomWorkspacePage.xaml"
if (-not (Test-Path $roomWorkspace)) {
    throw "Room operations must navigate to a real RoomWorkspacePage."
}
$roomsXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/Views/RoomsPage.xaml") -Raw
if ($roomsXaml -notmatch 'Content="OPEN ROOM"[^>]*Click="OnOpenRoom"') {
    throw "RoomsPage OPEN ROOM action is not wired to navigation."
}
$dashboardRoomXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/Views/DashboardPage.xaml") -Raw
if ($dashboardRoomXaml -notmatch 'Content="OPEN ROOM"[^>]*Click="OnOpenRoom"') {
    throw "Dashboard OPEN ROOM action is not wired to RoomWorkspace navigation."
}
$intakeXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/Views/IntakePage.xaml") -Raw
foreach ($action in @("ASSIGN / CHANGE", "CREATE ENTRY", "REJECT")) {
    if ($intakeXaml -notmatch ('Content="' + [regex]::Escape($action) + '"[^>]*Click=')) {
        throw "Intake action '$action' is not wired to a real handler."
    }
}
Write-Host "Validated operational room navigation and intake actions."

$appXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/App.xaml") -Raw
if ($appXaml -notmatch "XamlControlsResources") {
    throw "App.xaml must merge WinUI XamlControlsResources."
}
if ($appXaml -notmatch 'RequestedTheme="Dark"') {
    throw "Site Manager must request its dark control-room theme."
}
$mainXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/MainWindow.xaml") -Raw
if ($mainXaml -match 'Icon="Monitor"') {
    throw "NavigationView contains unsupported Symbol Monitor."
}
$dashboardXaml = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/Views/DashboardPage.xaml") -Raw
$dashboardCode = Get-Content (Join-Path $PSScriptRoot "../clients/site-manager/Views/DashboardPage.xaml.cs") -Raw
if ($dashboardXaml -match 'SizeChanged="PageSizeChanged"' -and $dashboardCode -notmatch 'PageSizeChanged\s*\(') {
    throw "DashboardPage has a dangling SizeChanged handler."
}
Write-Host "Validated WinUI resources, theme, icons, and event handlers."

$signageProjectPath = Join-Path $PSScriptRoot "../clients/windows/UPM.Signage/UPM.Signage.csproj"
if (-not (Test-Path $signageProjectPath)) { throw "Missing native UPM Signage project: $signageProjectPath" }
[xml]$signageProject = Get-Content $signageProjectPath
if ($signageProject.Project.PropertyGroup.OutputType -notcontains "WinExe" -or
    $signageProject.Project.PropertyGroup.AssemblyName -notcontains "UPM.Signage") {
    throw "UPM Signage must build as the independent UPM.Signage.exe WinExe."
}
$signageReferences = @($signageProject.Project.ItemGroup.ProjectReference.Include)
if ($signageReferences | Where-Object { $_ -match "RoomAgent|SiteManager" }) {
    throw "UPM Signage must not depend on Room Agent or Site Manager."
}
$signageSource = Get-ChildItem (Split-Path $signageProjectPath) -File | Get-Content -Raw
foreach ($forbidden in @("localStorage.operatorPassword", "?token=")) {
    if ($signageSource -match [regex]::Escape($forbidden)) { throw "Native Signage violates credential boundary: $forbidden" }
}
$playerSource = Get-Content (Join-Path $PSScriptRoot "../signage/player/index.html") -Raw
if ($playerSource -match "URLSearchParams\(location.search\).*token|localStorage\.token") {
    throw "The Signage player must not receive or persist credentials through its URL/browser storage."
}
Write-Host "Validated independent native Signage executable and credential boundaries."
