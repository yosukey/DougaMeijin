#ifndef AppName
  #define AppName "動画名人"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0" 
#endif
#ifndef AppInternalName
  #define AppInternalName "DougaMeijin"
#endif
#define AppCopyright "Copyright © 2025 Yosuke Yamazaki"



[Setup]
AppId={{969EEA7F-A5D1-47C0-B965-7718D1206D27}}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoCopyright={#AppCopyright}
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DefaultDirName={autopf}\{#AppInternalName}
OutputBaseFilename={#AppInternalName}-{#AppVersion}-{#Arch}-setup
Compression=lzma2/ultra64
SolidCompression=yes
ChangesAssociations=yes
WizardStyle=modern
UninstallDisplayIcon={app}\DougaMeijin.exe
InfoBeforeFile=warning.txt
LicenseFile=disclaimer.txt

[Files]
Source: "dist\DougaMeijin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\dmj_file.ico"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkablealone

[Registry]
; 1. 拡張子 .dmj を登録
Root: HKCR; Subkey: ".dmj"; ValueType: string; ValueName: ""; ValueData: "DougaMeijin.ProjectFile"; Flags: uninsdeletekey
; 2. プログラムのIDを作成
Root: HKCR; Subkey: "DougaMeijin.ProjectFile"; ValueType: string; ValueName: ""; ValueData: "動画名人 プロジェクト"; Flags: uninsdeletekey
; 3. デフォルトのアイコンを設定
Root: HKCR; Subkey: "DougaMeijin.ProjectFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\dmj_file.ico"
; 4. ダブルクリックしたときの動作を定義 ("%1" がファイルのパスになる)
Root: HKCR; Subkey: "DougaMeijin.ProjectFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\DougaMeijin.exe"" ""%1"""

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\DougaMeijin.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DougaMeijin.exe"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#AppInternalName}"

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"