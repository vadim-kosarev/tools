while ($true) {
    # ����� ������� �������
    & ".\internet-access.ps1"
    
    # ����� 1 ������ (60 ������)
    Start-Sleep -Seconds 60
}