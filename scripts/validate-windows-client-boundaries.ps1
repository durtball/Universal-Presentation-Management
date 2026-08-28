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
