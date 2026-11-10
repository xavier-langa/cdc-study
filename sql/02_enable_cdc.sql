-- Só para o sistema B. Requer SQL Server Agent a correr e edição Developer/Standard/Enterprise.
-- Correr com: sqlcmd -C -S localhost -U sa -P <password> -i sql/02_enable_cdc.sql
USE SalesSim;
GO
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = 'SalesSim' AND is_cdc_enabled = 1)
    EXEC sys.sp_cdc_enable_db;
GO
IF NOT EXISTS (SELECT 1 FROM cdc.change_tables WHERE source_object_id = OBJECT_ID('dbo.Documentos'))
    EXEC sys.sp_cdc_enable_table
        @source_schema = N'dbo',
        @source_name = N'Documentos',
        @role_name = NULL,
        @supports_net_changes = 0;
GO
