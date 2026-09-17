#define MyAppName "Moltage"
#define MyAppVersion "0.2.1"
#define MyAppPublisher "Junfeng Lin"
#define MyAppExeName "Moltage.exe"
#define MyAppIcon "..\resources\icons\moltage.ico"

[Setup]
AppId={{9C0D6515-2845-4213-9027-070FDED3EDA4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=0.2.1.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Moltage-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
InfoBeforeFile=..\THIRD_PARTY_NOTICES.md

[Files]
Source: "..\dist\windows\Moltage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[UninstallDelete]
Type: files; Name: "{autodesktop}\{#MyAppName}.lnk"

[Code]
var
  DesktopShortcutCheckBox: TNewCheckBox;

procedure InitializeWizard;
begin
  DesktopShortcutCheckBox := TNewCheckBox.Create(WizardForm);
  DesktopShortcutCheckBox.Parent := WizardForm.FinishedPage;
  DesktopShortcutCheckBox.Left := WizardForm.FinishedLabel.Left;
  DesktopShortcutCheckBox.Top :=
    WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(16);
  DesktopShortcutCheckBox.Width :=
    WizardForm.FinishedPage.ClientWidth - DesktopShortcutCheckBox.Left;
  DesktopShortcutCheckBox.Height := ScaleY(17);
  DesktopShortcutCheckBox.Caption := 'Create a desktop shortcut';
  DesktopShortcutCheckBox.Checked := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ShortcutPath: String;
begin
  Result := True;
  if CurPageID <> wpFinished then
    Exit;

  ShortcutPath := ExpandConstant('{autodesktop}\{#MyAppName}.lnk');
  if DesktopShortcutCheckBox.Checked then
  begin
    try
      CreateShellLink(
        ShortcutPath,
        '{#MyAppName}',
        ExpandConstant('{app}\{#MyAppExeName}'),
        '',
        ExpandConstant('{app}'),
        ExpandConstant('{app}\{#MyAppExeName}'),
        0,
        SW_SHOWNORMAL);
    except
      MsgBox(
        'The desktop shortcut could not be created.' #13#13 +
        GetExceptionMessage,
        mbError,
        MB_OK);
      Result := False;
    end;
  end
  else if FileExists(ShortcutPath) then
    DeleteFile(ShortcutPath);
end;
