' Lumberture Hub watchdog: if the server does not answer twice (30 s apart), restart it.
' Run every 2 minutes by the Windows task "LumbertureHubWatchdog" (serve.py creates the task once).
Option Explicit
Dim sh, fso, here, url, lastFile, t
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
url = "http://127.0.0.1:8081/login"

Function Alive()
  On Error Resume Next
  Dim x
  Alive = False
  Set x = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  x.setTimeouts 5000, 5000, 20000, 20000
  x.open "GET", url, False
  x.send
  If Err.Number = 0 Then
    If x.status > 0 And x.status < 500 Then Alive = True
  End If
  Err.Clear
End Function

If Alive() Then WScript.Quit 0
WScript.Sleep 30000
If Alive() Then WScript.Quit 0

' restarted less than 3 minutes ago - wait
lastFile = here & "\data\watchdog_last.txt"
If fso.FileExists(lastFile) Then
  If DateDiff("s", fso.GetFile(lastFile).DateLastModified, Now) < 180 Then WScript.Quit 0
End If
Set t = fso.CreateTextFile(lastFile, True)
t.WriteLine Now & " watchdog: no answer, restart"
t.Close
Set t = fso.OpenTextFile(here & "\data\restart_log.txt", 8, True)
t.WriteLine Now & " watchdog restart"
t.Close
sh.CurrentDirectory = here
sh.Run "cmd /c """ & here & "\qayta_ishga_tushirish.bat""", 0, False
