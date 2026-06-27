#!/usr/bin/env python3
"""
Utilitários centralizados para evitar duplicação de código.
- logger setup
- RPC helpers
- keypair loading
- retry logic
"""

import logging
import time
import sys
import json
import base58
import requests
from pathlib import Path
from typing import Optional, Dict, Any

# Detecta ambiente
try:
    import config as Config
except ImportError:
    Config = None


def setup_logger(name: str, level=logging.INFO) -> logging.Logger:
    """
    Configura logger centralizado com handlers para console + arquivo.

    Args:
        name: Nome do logger (ex: "trader", "analyzer")
        level: Nível de logging (default: INFO)

    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Evita duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)

    # File handler (se Config disponível)
    if Config:
        try:
            log_file = Config.LOGS_DIR / f"{name}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setLevel(level)
            logger.addHandler(fh)
        except:
            pass

    # Formatter
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    return logger


class RpcClient:
    """Cliente RPC com retry logic e cache."""

    def __init__(self, rpc_url: str, timeout: int = 10, max_retries: int = 3):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.logger = setup_logger("RpcClient")

    def call(self, method: str, params: list = None, **kwargs) -> Optional[Any]:
        """
        Faz chamada RPC com retry exponencial.

        Args:
            method: Nome do método RPC (ex: "getBalance")
            params: Parâmetros (lista)

        Returns:
            Resultado ou None se falhou
        """
        if params is None:
            params = []

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }

        for retry in range(self.max_retries):
            try:
                r = requests.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout
                )
                data = r.json()

                if "error" in data:
                    self.logger.warning(f"RPC error ({method}): {data['error']}")
                    return None

                return data.get("result")

            except requests.Timeout:
                if retry < self.max_retries - 1:
                    wait = 2 ** retry
                    self.logger.warning(f"RPC timeout, retry in {wait}s...")
                    time.sleep(wait)
            except Exception as e:
                self.logger.error(f"RPC call failed: {e}")
                if retry < self.max_retries - 1:
                    time.sleep(2 ** retry)

        return None

    def get_balance(self, address: str) -> float:
        """Get SOL balance em lamports (retorna em SOL)."""
        result = self.call("getBalance", [address])
        if result is None:
            return 0.0
        return (result.get("value", 0) or 0) / 1e9

    def get_account(self, address: str) -> Optional[Dict]:
        """Get account info."""
        return self.call("getAccountInfo", [address, {"encoding": "base64"}])


class KeypairManager:
    """Gerenciador de keypairs com validação."""

    def __init__(self):
        self.logger = setup_logger("KeypairManager")

    def load_keypair_from_env(self, env_var: str):
        """
        Carrega keypair de environment variable (base58).

        Args:
            env_var: Nome da env var (ex: "WALLET_1_KEY")

        Returns:
            Keypair object ou None
        """
        import os
        from solders.keypair import Keypair

        key_str = os.environ.get(env_var)
        if not key_str:
            self.logger.warning(f"{env_var} não configurada")
            return None

        try:
            kp = Keypair.from_bytes(base58.b58decode(key_str))
            self.logger.info(f"✓ {env_var} carregada: {str(kp.pubkey())[:20]}...")
            return kp
        except Exception as e:
            self.logger.error(f"Erro ao carregar {env_var}: {e}")
            return None

    def load_all_keypairs(self, env_vars: list) -> list:
        """
        Carrega múltiplos keypairs.

        Args:
            env_vars: Lista de nomes de env vars

        Returns:
            Lista de {index, kp, addr}
        """
        keypairs = []
        for idx, env_var in enumerate(env_vars, 1):
            kp = self.load_keypair_from_env(env_var)
            if kp:
                keypairs.append({
                    "index": idx,
                    "kp": kp,
                    "addr": str(kp.pubkey())
                })

        if not keypairs:
            self.logger.error("Nenhum keypair carregado!")
            sys.exit(1)

        return keypairs


def retry_with_backoff(
    func,
    args=None,
    kwargs=None,
    max_retries: int = 3,
    base_wait: float = 1.0
) -> Optional[Any]:
    """
    Executa função com retry exponencial.

    Args:
        func: Função a executar
        args: Argumentos posicionais
        kwargs: Argumentos nomeados
        max_retries: Número máximo de tentativas
        base_wait: Espera inicial em segundos

    Returns:
        Resultado da função ou None se falhou
    """
    logger = setup_logger("retry_with_backoff")

    if args is None:
        args = ()
    if kwargs is None:
        kwargs = {}

    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = base_wait * (2 ** attempt)
                logger.warning(f"Tentativa {attempt + 1} falhou: {e}. Retry em {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"Todas as tentativas falharam: {e}")

    return None


def safe_json_dump(obj: Any, file_path: Path, indent: int = 2) -> bool:
    """
    Salva JSON de forma segura (atomic write com backup).

    Args:
        obj: Objeto a serializar
        file_path: Caminho do arquivo
        indent: Indentação JSON

    Returns:
        True se sucesso, False se falhou
    """
    logger = setup_logger("safe_json_dump")

    try:
        # Backup se arquivo existe
        if file_path.exists():
            backup = file_path.with_suffix(file_path.suffix + '.bak')
            file_path.rename(backup)

        # Escrita atômica (escreve em temp depois move)
        temp = file_path.with_suffix(file_path.suffix + '.tmp')
        with open(temp, 'w') as f:
            json.dump(obj, f, indent=indent)

        temp.rename(file_path)
        logger.info(f"JSON salvo: {file_path}")
        return True

    except Exception as e:
        logger.error(f"Erro ao salvar JSON: {e}")
        return False


def safe_json_load(file_path: Path) -> Optional[Dict]:
    """
    Carrega JSON de forma segura com fallback para backup.

    Args:
        file_path: Caminho do arquivo

    Returns:
        Dict ou None se falhou
    """
    logger = setup_logger("safe_json_load")

    try:
        if file_path.exists():
            with open(file_path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Erro ao carregar {file_path}: {e}")

        # Tenta backup
        backup = file_path.with_suffix(file_path.suffix + '.bak')
        if backup.exists():
            try:
                with open(backup, 'r') as f:
                    logger.info(f"Carregado backup: {backup}")
                    return json.load(f)
            except:
                pass

    return None


# Exports
__all__ = [
    'setup_logger',
    'RpcClient',
    'KeypairManager',
    'retry_with_backoff',
    'safe_json_dump',
    'safe_json_load'
]
