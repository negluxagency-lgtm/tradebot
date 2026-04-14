# 📜 Directiva: Configuración de Entorno Polymarket

## 🚀 Misión
Establecer un entorno de desarrollo seguro e idempotent para el bot de trading de Polymarket.

## 🛠️ Herramientas y SDKs
- SDK: `py-clob-client`
- Autenticación: Relayer API (CLOB v2)
- Librerías: `python-dotenv`, `web3`, `requests`

## 📋 Protocolo de Ejecución
1. Verificar pre-requisitos (Python 3.10+).
2. Configurar `.env.local` con `RELAYER_API_KEY` y `RELAYER_API_KEY_ADDRESS`.
3. Validar la carga de variables de entorno para las cabeceras del Relayer.

## ⚠️ Restricciones
- El archivo `.env.local` **NUNCA** debe ser subido a un repositorio público.
- Usar `os.getenv` para acceder a las claves.
- La red por defecto es **Polygon Mainnet (Chain ID: 137)**.

## 📂 Estructura de Salida
- `.env.local`: Almacén de API Keys.
- `src/setup_env.py`: Script de verificación técnica.

## 🛰️ Bitácora de Anomalías
| Fecha      | Error Detectado | Solución Aplicada |
|------------|----------------|-------------------|
| 2026-03-29 | Inicio Misión   | N/A               |
