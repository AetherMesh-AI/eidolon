' Hermes Agent Gateway - Messaging Platform Integration
Option Explicit
Dim fso, sh, target
target = "@TARGET@"
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(target) Then WScript.Quit 0
Set sh = CreateObject("WScript.Shell")
sh.Run "wscript.exe ""@TARGET@""", 0, False
