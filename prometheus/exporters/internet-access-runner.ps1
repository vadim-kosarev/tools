while ($true) {
    # ����� ������� �������
    & "C:\Tools\prometheus\exporters\internet-access.ps1"
    
    # ����� 1 ������ (60 ������)
    Start-Sleep -Seconds 60
}