' ======================================================================
' Telegram Companion AI - Запуск в фоновом режиме (без окна консоли)
' ======================================================================
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
' Запуск run_bot.bat в скрытом режиме (0 = Hide window, False = don't wait)
WshShell.Run "cmd.exe /c """ & strPath & "\run_bot.bat""", 0, False
Set WshShell = Nothing
Set fso = Nothing
