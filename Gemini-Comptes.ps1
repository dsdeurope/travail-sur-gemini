#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Menu','Ajouter','Lister','Copier','Exporter','Installer')][string]$Action='Menu', [string]$Email)
$ErrorActionPreference='Stop'
$storePath=Join-Path $PSScriptRoot 'comptes-proteges.json'

function Initialize-Google {
    $runtime=Join-Path (Split-Path $PSScriptRoot -Parent) 'work\google-runtime'
    $portable=Join-Path $runtime 'google-cloud-sdk\bin\gcloud.cmd'
    $ready=Join-Path $runtime 'installation-complete.txt'
    $env:CLOUDSDK_CONFIG=Join-Path $runtime 'connexion'
    $env:CLOUDSDK_CORE_DISABLE_USAGE_REPORTING='true'
    $env:CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK='true'
    if (-not (Test-Path -LiteralPath $portable) -or -not (Test-Path -LiteralPath $ready)) {
        if (-not [Environment]::Is64BitOperatingSystem) { throw 'Cette version requiert Windows 64 bits.' }
        New-Item -ItemType Directory -Path $runtime -Force | Out-Null
        $archive=Join-Path $runtime 'google-cloud-sdk.zip'
        Write-Host 'Preparation automatique de Google Cloud (environ 103 Mo, premiere utilisation seulement)...'
        [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
        $expected='5fecb9af0f178331b2333c167e57a2676ac286029fb16e328aef911f9d2df2a8'
        if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
            $download=New-Object Net.WebClient
            try { $download.DownloadFile('https://storage.googleapis.com/cloud-sdk-release/google-cloud-sdk-586.0.0-windows-x86_64-bundled-python.zip',$archive) }
            finally { $download.Dispose() }
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Archive Google non conforme : installation interrompue.' }
        Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
    }
    $script:google=$portable
    if (-not (Test-Path -LiteralPath $portable)) { throw 'La preparation de Google Cloud a echoue.' }
    $null=Invoke-Google @('version','--format=json')
    [IO.File]::WriteAllText($ready,'586.0.0')
}

function Read-Store {
    if (Test-Path -LiteralPath $storePath) {
        @(Get-Content -LiteralPath $storePath -Raw | ConvertFrom-Json)
    }
}
function Save-Store($Rows) {
    $temporary="$storePath.tmp"
    ConvertTo-Json -InputObject @($Rows) -Depth 6 | Set-Content -LiteralPath $temporary -Encoding UTF8
    if (Test-Path -LiteralPath $storePath) { [IO.File]::Replace($temporary,$storePath,"$storePath.bak") }
    else { [IO.File]::Move($temporary,$storePath) }
}
function Invoke-Google([string[]]$Arguments) {
    # Do not merge stderr into stdout: stdout can contain a secret or JSON.
    $oldPreference=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    try {
        $output=& $script:google @Arguments
        $resultCode=$LASTEXITCODE
    } finally { $ErrorActionPreference=$oldPreference }
    if ($resultCode -ne 0) { throw "Google Cloud a refuse l'operation (code $resultCode). Consulte le message ci-dessus puis relance Ajouter pour reprendre." }
    $output -join "`n"
}
function Select-Email {
    if (-not $script:Email) { $script:Email=Read-Host 'Adresse du compte Google' }
    $script:Email=$script:Email.Trim().ToLowerInvariant()
    if ($script:Email -notmatch '^[a-z0-9][a-z0-9._%+\-]*@[a-z0-9.\-]+\.[a-z]{2,}$') { throw 'Adresse email invalide.' }
}
function Add-Account {
    Select-Email
    $rows=@(Read-Store)
    $entry=$rows | Where-Object { $_.Email -eq $script:Email } | Select-Object -First 1
    if ($entry -and $entry.Secret) { Export-Keys; Write-Host 'La cle de ce compte est deja dans la liste.'; return }
    Initialize-Google
    Write-Host 'Connecte-toi dans la page Google qui va ouvrir. Le mot de passe reste chez Google.'
    $null=Invoke-Google @('auth','login',$script:Email,'--no-activate','--quiet')
    $accounts=Invoke-Google @('auth','list','--format=json') | ConvertFrom-Json
    if (-not (@($accounts.account) -contains $script:Email)) { throw 'Le compte attendu ne figure pas parmi les comptes connectes.' }
    Write-Host 'Verification de la connexion du compte aupres de Google...'
    $accessToken=Invoke-Google @('auth','print-access-token',"--account=$($script:Email)",'--quiet')
    if ([string]::IsNullOrWhiteSpace($accessToken)) { throw 'Google ne confirme pas la connexion de ce compte. Aucun projet ne sera cree.' }
    $accessToken=$null
    Write-Host 'Connexion Google validee. Creation automatique en cours...'
    if (-not $entry) {
        $entry=[pscustomobject]@{Email=$script:Email; Project=('gemini-'+[guid]::NewGuid().ToString('N').Substring(0,20)); ProjectCreated=$false; KeyId='gemini-local'; Secret=''; Status='En cours'}
        $rows+= $entry
        Save-Store $rows
    }
    $common=@("--account=$($entry.Email)","--project=$($entry.Project)",'--quiet')
    if (-not $entry.ProjectCreated) {
        # Discover a project created just before an interrupted local save.
        $projects=Invoke-Google (@('projects','list','--format=json')+$common) | ConvertFrom-Json
        if (-not (@($projects.projectId) -contains $entry.Project)) {
            Write-Host "Creation du projet $($entry.Project)..."
            $null=Invoke-Google (@('projects','create',$entry.Project,'--name=Gemini personnel')+$common)
        }
        $entry.ProjectCreated=$true
        Save-Store $rows
    }
    Write-Host 'Activation des API...'
    $null=Invoke-Google (@('services','enable','apikeys.googleapis.com','generativelanguage.googleapis.com','iam.googleapis.com')+$common)
    $serviceEmail="gemini-local@$($entry.Project).iam.gserviceaccount.com"
    $serviceAccounts=Invoke-Google (@('iam','service-accounts','list','--format=json')+$common) | ConvertFrom-Json
    if (-not (@($serviceAccounts.email) -contains $serviceEmail)) {
        $null=Invoke-Google (@('iam','service-accounts','create','gemini-local','--display-name=Gemini personnel')+$common)
    }
    $keys=@(Invoke-Google (@('services','api-keys','list','--format=json')+$common) | ConvertFrom-Json)
    $key=$keys | Where-Object { ($_.name -split '/')[-1] -eq $entry.KeyId } | Select-Object -First 1
    if (-not $key) {
        Write-Host 'Creation de la cle limitee a Gemini...'
        $null=Invoke-Google (@('services','api-keys','create',"--key-id=$($entry.KeyId)","--service-account=$serviceEmail",'--display-name=Gemini personnel','--api-target=service=generativelanguage.googleapis.com','--format=json')+$common)
    }
    $plain=Invoke-Google (@('services','api-keys','get-key-string',$entry.KeyId,'--location=global','--format=value(keyString)')+$common)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw 'Google a retourne une cle vide. Relance Ajouter pour reprendre.' }
    if (-not $plain.Trim().StartsWith('AQ')) { throw 'La cle retournee ne commence pas par AQ. Aucun export : verifier le type de cle dans Google AI Studio.' }
    $entry.Secret=ConvertFrom-SecureString (ConvertTo-SecureString $plain.Trim() -AsPlainText -Force)
    $plain=$null
    $entry.Status='Cle creee'
    Save-Store $rows
    Export-Keys
    Write-Host "Termine : $($entry.Email) / $($entry.Project). La cle est chiffree localement."
}
function List-Accounts {
    $rows=@(Read-Store)
    if (-not $rows.Count) { Write-Host 'Aucun compte enregistre.'; return }
    $rows | Select-Object Email,Project,Status | Format-Table -AutoSize
}
function Export-Keys {
    $lines=@(foreach ($entry in @(Read-Store)) {
        if (-not $entry.Secret) { continue }
        $secure=ConvertTo-SecureString $entry.Secret
        $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $value=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            if (-not $value.StartsWith('AQ')) { throw 'Export interrompu : une cle ne commence pas par AQ.' }
            $value
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    })
    if (-not $lines.Count) { Write-Host 'Aucune cle a exporter.'; return }
    $exportPath=Join-Path $PSScriptRoot 'cles-gemini-AQ.txt'
    [IO.File]::WriteAllLines($exportPath,[string[]]$lines,(New-Object Text.UTF8Encoding($false)))
    Write-Host "Liste exportee : $exportPath (cles en clair, une par ligne)."
}
function Copy-Key {
    Select-Email
    $entry=@(Read-Store) | Where-Object { $_.Email -eq $script:Email } | Select-Object -First 1
    if (-not $entry -or -not $entry.Secret) { throw 'Aucune cle enregistree pour ce compte.' }
    $secure=ConvertTo-SecureString $entry.Secret
    $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { Set-Clipboard -Value ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    Write-Host 'Cle copiee dans le presse-papiers.'
}
if ($Action -ne 'Menu') {
    switch ($Action) { 'Ajouter' { Add-Account }; 'Lister' { List-Accounts }; 'Copier' { Copy-Key }; 'Exporter' { Export-Keys }; 'Installer' { Initialize-Google } }
    exit
}
while ($true) {
    Write-Host "`nGEMINI - MES COMPTES`n1. Ajouter un compte / reprendre`n2. Lister les comptes`n3. Copier une cle`n4. Quitter`n5. Exporter la liste AQ"
    $choice=Read-Host 'Choix'
    if ($choice -eq '4') { break }
    try {
        $script:Email=''
        switch ($choice) { '1' { Add-Account }; '2' { List-Accounts }; '3' { Copy-Key }; '5' { Export-Keys }; default { Write-Host 'Choix inconnu.' } }
    } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
}
