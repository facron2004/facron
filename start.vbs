Option Explicit



Dim sh, fso, root, logDir, logFile

Set sh = CreateObject("WScript.Shell")

Set fso = CreateObject("Scripting.FileSystemObject")



root = fso.GetParentFolderName(WScript.ScriptFullName) & "\"

logDir = root & "logs"

logFile = root & "start.log"



If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)



sh.CurrentDirectory = root

AppendLog "=== start run at " & Now & " ==="



If Not fso.FileExists(root & ".venv\Scripts\python.exe") Then

  AppendLog "[ERROR] .venv not found"

  WScript.Quit 1

End If



' Auto-create default .env if missing (SQLite + local Redis)

If Not fso.FileExists(root & ".env") Then

  Dim envFile

  Set envFile = fso.CreateTextFile(root & ".env", True)

  envFile.WriteLine "DATABASE_URL=sqlite:///./data/app.db"

  envFile.WriteLine "REDIS_URL=redis://localhost:6379/0"

  envFile.Close

End If



If Not IsPortFree(8000) Then

  AppendLog "[ERROR] Port 8000 already in use"

  WScript.Quit 1

End If



If Not IsRedisUp() Then

  RunHidden "cmd /c ""cd /d """ & root & """ && if exist tools\redis\redis-server.exe tools\redis\redis-server.exe tools\redis\redis.windows.conf >> """ & logDir & "\redis.log"" 2>&1"""

End If



' alembic upgrade head — single source of truth for schema

RunHidden "cmd /c ""cd /d """ & root & """ && call .venv\Scripts\activate.bat && python -m alembic upgrade head >> """ & logFile & """ 2>&1"""

RunHidden "cmd /c ""cd /d """ & root & """ && call .venv\Scripts\activate.bat && celery -A review_scraper.workers.celery_app worker --loglevel=info --pool=solo --concurrency=1 -Q scrape,default >> """ & logDir & "\celery_worker.log"" 2>&1"""

RunHidden "cmd /c ""cd /d """ & root & """ && call .venv\Scripts\activate.bat && python -m uvicorn review_scraper.main:app --host 127.0.0.1 --port 8000 >> """ & logFile & "" 2>&1"""



WScript.Sleep 2500

OpenBrowser "http://127.0.0.1:8000/"

AppendLog "[OK] browser opened"



Sub RunHidden(commandText)

  sh.Run commandText, 0, False

End Sub



Sub OpenBrowser(url)

  On Error Resume Next

  sh.Run "cmd /c start """" " & url, 0, False

  On Error GoTo 0

End Sub



Sub AppendLog(message)

  Dim ts

  Set ts = fso.OpenTextFile(logFile, 8, True)

  ts.WriteLine message

  ts.Close

End Sub



Function IsRedisUp()

  On Error Resume Next

  Dim exec

  Set exec = sh.Exec("cmd /c python -c ""import socket;s=socket.socket();s.settimeout(0.5);s.connect(('127.0.0.1',6379));s.close()""")

  Do While exec.Status = 0

    WScript.Sleep 50

  Loop

  IsRedisUp = (exec.ExitCode = 0)

  On Error GoTo 0

End Function



Function IsPortFree(port)

  On Error Resume Next

  Dim exec, outText

  Set exec = sh.Exec("cmd /c netstat -ano ^| findstr :" & port & " ^| findstr LISTENING")

  outText = exec.StdOut.ReadAll

  IsPortFree = (InStr(outText, "LISTENING") = 0)

  On Error GoTo 0

End Function

