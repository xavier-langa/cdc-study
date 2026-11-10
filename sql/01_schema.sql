IF DB_ID('SalesSim') IS NULL
    CREATE DATABASE SalesSim;
GO
USE SalesSim;
GO
IF OBJECT_ID('dbo.Documentos') IS NULL
CREATE TABLE dbo.Documentos (
    DocId      BIGINT IDENTITY(1,1) PRIMARY KEY,
    Tipo       CHAR(2)       NOT NULL CHECK (Tipo IN ('FT','NC')),  -- FT factura, NC nota de crédito
    Data       DATE          NOT NULL,
    VendedorId INT           NOT NULL,
    ClienteId  INT           NOT NULL,
    Total      DECIMAL(18,2) NOT NULL,
    Version    INT           NOT NULL DEFAULT 1,                    -- incrementa em cada alteração
    ChangedAt  DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME()      -- relógio do SQL Server (UTC)
);
GO
