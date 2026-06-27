#!/usr/bin/env python3
"""
HTTP Client centralizado com retry logic, timeouts, e error handling.
Elimina duplicação de código de requisições.
"""

import requests
import time
from typing import Optional, Dict, Any, List
from utils import setup_logger

logger = setup_logger("HttpClient")


class HttpClient:
    """Cliente HTTP com retry, timeout, e rate limiting."""

    def __init__(
        self,
        timeout: int = 10,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        rate_limit_delay: float = 0.0
    ):
        """
        Args:
            timeout: Timeout em segundos por requisição
            max_retries: Número máximo de tentativas
            backoff_base: Base para exponential backoff (delay = base * 2^attempt)
            rate_limit_delay: Delay entre requisições (para respeitar rate limit)
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0.0

    def _wait_for_rate_limit(self) -> None:
        """Respeita rate limiting."""
        if self.rate_limit_delay > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.rate_limit_delay:
                time.sleep(self.rate_limit_delay - elapsed)

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Optional[requests.Response]:
        """
        GET request com retry automático.

        Args:
            url: URL
            params: Query parameters
            headers: Headers customizados
            **kwargs: Argumentos adicionais para requests.get

        Returns:
            Response object ou None se falhou após retries
        """
        return self._request("GET", url, params=params, headers=headers, **kwargs)

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Optional[requests.Response]:
        """
        POST request com retry automático.

        Args:
            url: URL
            data: Form data
            json: JSON data
            headers: Headers customizados
            **kwargs: Argumentos adicionais para requests.post

        Returns:
            Response object ou None se falhou após retries
        """
        return self._request(
            "POST",
            url,
            data=data,
            json=json,
            headers=headers,
            **kwargs
        )

    def _request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Optional[requests.Response]:
        """
        Executa requisição com retry exponencial.

        Args:
            method: GET, POST, etc
            url: URL
            **kwargs: Argumentos para requests

        Returns:
            Response object ou None se falhou
        """
        kwargs.setdefault("timeout", self.timeout)

        for attempt in range(self.max_retries):
            try:
                # Rate limit
                self._wait_for_rate_limit()
                self.last_request_time = time.time()

                # Requisição
                response = requests.request(method, url, **kwargs)

                # 2xx/3xx = sucesso
                if 200 <= response.status_code < 400:
                    logger.debug(f"{method} {url} → {response.status_code}")
                    return response

                # 4xx = erro do cliente (não retry)
                if 400 <= response.status_code < 500:
                    logger.warning(f"{method} {url} → {response.status_code}")
                    return response

                # 5xx = erro do servidor (retry)
                if response.status_code >= 500:
                    if attempt < self.max_retries - 1:
                        wait = self.backoff_base * (2 ** attempt)
                        logger.warning(
                            f"{method} {url} → {response.status_code}. "
                            f"Retry {attempt + 1}/{self.max_retries} em {wait}s..."
                        )
                        time.sleep(wait)
                        continue

                return response

            except requests.Timeout:
                if attempt < self.max_retries - 1:
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        f"{method} {url} timeout. "
                        f"Retry {attempt + 1}/{self.max_retries} em {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"{method} {url} timeout after {self.max_retries} attempts")

            except requests.ConnectionError as e:
                if attempt < self.max_retries - 1:
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        f"{method} {url} connection error: {e}. "
                        f"Retry {attempt + 1}/{self.max_retries} em {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"{method} {url} connection failed: {e}")

            except Exception as e:
                logger.error(f"{method} {url} error: {e}")
                return None

        logger.error(f"{method} {url} failed after {self.max_retries} retries")
        return None

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        default: Any = None,
        **kwargs
    ) -> Any:
        """
        GET e retorna JSON parsed.

        Args:
            url: URL
            params: Query params
            headers: Custom headers
            default: Valor padrão se falhar ou JSON inválido
            **kwargs: Argumentos adicionais

        Returns:
            JSON parsed ou default value
        """
        response = self.get(url, params=params, headers=headers, **kwargs)

        if not response:
            return default

        try:
            return response.json()
        except Exception as e:
            logger.warning(f"JSON parse error for {url}: {e}")
            return default

    def post_json(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        default: Any = None,
        **kwargs
    ) -> Any:
        """
        POST e retorna JSON parsed.

        Args:
            url: URL
            json: Dados JSON
            headers: Custom headers
            default: Valor padrão se falhar
            **kwargs: Argumentos adicionais

        Returns:
            JSON parsed ou default value
        """
        response = self.post(url, json=json, headers=headers, **kwargs)

        if not response:
            return default

        try:
            return response.json()
        except Exception as e:
            logger.warning(f"JSON parse error for {url}: {e}")
            return default


# Clients singleton para diferentes propósitos
_clients: Dict[str, HttpClient] = {}


def get_client(
    name: str = "default",
    timeout: int = 10,
    max_retries: int = 3,
    rate_limit_delay: float = 0.0
) -> HttpClient:
    """
    Obtém ou cria cliente HTTP com cache.

    Args:
        name: Nome do cliente
        timeout: Timeout em segundos
        max_retries: Número de retries
        rate_limit_delay: Delay entre requisições

    Returns:
        HttpClient instance
    """
    if name not in _clients:
        _clients[name] = HttpClient(
            timeout=timeout,
            max_retries=max_retries,
            rate_limit_delay=rate_limit_delay
        )

    return _clients[name]


# Exports
__all__ = ['HttpClient', 'get_client']
