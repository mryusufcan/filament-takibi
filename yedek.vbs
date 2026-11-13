' yedek.ps1'i pencere gostermeden calistirir (gorev her 10 dakikada bir tetikler).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & fso.GetParentFolderName(WScript.ScriptFullName) & "\yedek.ps1""", 0, False
