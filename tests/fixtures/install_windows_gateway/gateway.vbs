' Hermes Agent Gateway - Messaging Platform Integration
Option Explicit
Dim sh, env, existing_pp
Set sh = CreateObject("WScript.Shell")
Set env = sh.Environment("PROCESS")
env.Item("HERMES_HOME") = "@HOME@"
env.Item("PYTHONIOENCODING") = "utf-8"
env.Item("HERMES_GATEWAY_DETACHED") = "1"
env.Item("HERMES_SUPERVISED_CHILD") = "1"
env.Item("VIRTUAL_ENV") = "@VENV@"
existing_pp = env.Item("PYTHONPATH")
If Len(existing_pp) > 0 Then
  env.Item("PYTHONPATH") = "@PYTHONPATH@;" & existing_pp
Else
  env.Item("PYTHONPATH") = "@PYTHONPATH@"
End If
sh.CurrentDirectory = "@HOME@"
sh.Run """@PYTHON@"" -m @MODULE@ @PROFILE@gateway run", 0, False
