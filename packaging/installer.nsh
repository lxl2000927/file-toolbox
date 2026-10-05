; Keep the installation marker with the tracked packaging sources. Portable
; and ZIP distributions do not run these hooks and remain manual-update builds.
!macro customInstall
  Push $0
  ClearErrors
  FileOpen $0 "$INSTDIR\.file-toolbox-installed" w
  ${IfNot} ${Errors}
    FileWrite $0 "File Toolbox installer$\r$\n"
    FileClose $0
  ${EndIf}
  Pop $0
!macroend

!macro customUnInstall
  Delete "$INSTDIR\.file-toolbox-installed"
!macroend
