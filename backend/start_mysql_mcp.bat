@echo off
setlocal

set "ENV_FILE=%~dp0\.env"

if not exist "%ENV_FILE%" (
    echo [ERROR] Missing .env file: %ENV_FILE%
    exit /b 1
)

REM Load KEY=VALUE lines from .env (skip blank lines and # comments).
for /f "usebackq eol=# tokens=1* delims==" %%a in ("%ENV_FILE%") do (
    set "%%a=%%b"
)

REM Optional -stdio switch overrides transport.
if /i "%~1"=="-stdio" set "MCP_TRANSPORT=stdio"

if "%MCP_TRANSPORT%"=="" set "MCP_TRANSPORT=sse"

echo Transport: %MCP_TRANSPORT%
if /i "%MCP_TRANSPORT%"=="sse" (
    echo   SSE endpoint: http://%MCP_SSE_HOST%:%PORT%/sse
)
echo MySQL: %MYSQL_USER%@%MYSQL_HOST%:%MYSQL_PORT%  database='%MYSQL_DATABASE%'

uvx --from mysql-mcp-server mysql_mcp_server
