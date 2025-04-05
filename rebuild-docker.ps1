#!/usr/bin/env pwsh
#
# Rebuild and run StatSource MCP Docker container
# Usage: ./rebuild-docker.ps1 [options]
#
# Options:
#   -ImageName <name>     Docker image name (default: statsource/mcp)
#   -Push                 Push to Docker Hub after building
#   -LogLevel <level>     Set logging level (DEBUG, INFO, WARNING, ERROR)
#   -ApiKey <key>         Set API key for authentication
#   -DbConnection <conn>  Set database connection string
#   -RunContainer         Run the container after building (default: false)
#   -ServerVersion <ver>  Set server version tag (default: development)

param (
    [string]$ImageName = "statsource/mcp",
    [switch]$Push = $false,
    [string]$LogLevel = "",
    [string]$ApiKey = "",
    [string]$DbConnection = "",
    [switch]$RunContainer = $false,
    [string]$ServerVersion = "development"
)

# Make sure we're in the right directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Set up error handling
$ErrorActionPreference = "Stop"

# Display startup banner
Write-Host "=======================================" -ForegroundColor Blue
Write-Host "  StatSource MCP Docker Build Script   " -ForegroundColor Blue
Write-Host "=======================================" -ForegroundColor Blue
Write-Host "Image name:    $ImageName"
Write-Host "Log level:     $(if ($LogLevel) {$LogLevel} else {"[DISABLED]"})"
Write-Host "Server version: $ServerVersion"
Write-Host "API key:       $(if ($ApiKey) {"[CONFIGURED]"} else {"[NOT CONFIGURED]"})"
Write-Host "DB connection: $(if ($DbConnection) {"[CONFIGURED]"} else {"[NOT CONFIGURED]"})"
Write-Host "Run container: $RunContainer"
Write-Host "Push to hub:   $Push"
Write-Host "======================================="
Write-Host ""

try {
    # Check if Docker is running
    $dockerStatus = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running. Please start Docker and try again."
    }

    # Build the Docker image
    Write-Host "Building Docker image '$ImageName'..." -ForegroundColor Yellow
    docker build -t $ImageName --build-arg SERVER_VERSION=$ServerVersion .

    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "Docker image built successfully!" -ForegroundColor Green

    # Push to Docker Hub if requested
    if ($Push) {
        Write-Host "Pushing '$ImageName' to Docker Hub..." -ForegroundColor Yellow
        docker push $ImageName
        
        if ($LASTEXITCODE -ne 0) {
            throw "Docker push failed with exit code $LASTEXITCODE. Make sure you're logged in with 'docker login'"
        }
        
        Write-Host "Docker image pushed successfully!" -ForegroundColor Green
    }

    # Run the container if requested
    if ($RunContainer) {
        Write-Host "Running container from image '$ImageName'..." -ForegroundColor Yellow
        
        $env_vars = @()
        if ($LogLevel) {
            $env_vars += "-e", "LOG_LEVEL=$LogLevel"
        }
        if ($ServerVersion) {
            $env_vars += "-e", "SERVER_VERSION=$ServerVersion"
        }
        if ($ApiKey) {
            $env_vars += "-e", "API_KEY=$ApiKey"
        }
        if ($DbConnection) {
            $env_vars += "-e", "DB_CONNECTION_STRING=$DbConnection"
            $env_vars += "-e", "DB_SOURCE_TYPE=$DbSourceType"
        }
        
        # Create Docker run command
        $docker_run_cmd = @("docker", "run", "-i", "--rm") + $env_vars + @($ImageName)
        
        # Display the command (with masked sensitive data)
        $displayCmd = $docker_run_cmd -replace "API_KEY=.*?(?=\s|$)", "API_KEY=***" -replace "DB_CONNECTION_STRING=.*?(?=\s|$)", "DB_CONNECTION_STRING=***"
        Write-Host "Executing: $($displayCmd -join ' ')" -ForegroundColor Gray
        
        # Run the docker command
        & $docker_run_cmd[0] $docker_run_cmd[1..($docker_run_cmd.Length-1)]
        
        if ($LASTEXITCODE -ne 0) {
            throw "Docker run failed with exit code $LASTEXITCODE"
        }
    }
    
    Write-Host "Script completed successfully!" -ForegroundColor Green

} catch {
    Write-Host "ERROR: $_" -ForegroundColor Red
    exit 1
} 