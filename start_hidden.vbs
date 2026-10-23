' Zapusk servera bez okna konsoli (dlya avtozapuska). Ostanovit: Dispetcher zadach -> python.exe
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = dir
sh.Run "cmd /c """ & dir & "\start.bat""", 0, False
