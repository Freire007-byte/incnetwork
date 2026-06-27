#!/usr/bin/env python3
"""
Parallel Executor — Phase 3
✓ ThreadPoolExecutor para análises
✓ Batch processing otimizado
✓ -60% execution time expected
✓ Safe com locks para shared data
"""

import threading
import time
import sys, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import List, Callable, Any, Dict

sys.path.insert(0, str(Path(__file__).parent))
import config as Config

class ParallelAnalyzer:
    """Executor paralelo para análises de tokens."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.Lock()
        self.results = []
        self.errors = []

    def analyze_batch(self, items: List[Any], analyze_func: Callable) -> Dict:
        """
        Analisa múltiplos items em paralelo.

        analyze_func(item) -> resultado
        """
        futures = {}
        start_time = time.time()

        # Submete todos os jobs
        for item in items:
            future = self.executor.submit(analyze_func, item)
            futures[future] = item

        # Aguarda conclusão
        results = []
        for future in as_completed(futures, timeout=60):
            item = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                with self.lock:
                    self.errors.append({"item": item, "error": str(e)})

        elapsed = time.time() - start_time

        return {
            "total": len(items),
            "completed": len(results),
            "failed": len(self.errors),
            "elapsed_sec": elapsed,
            "avg_per_item_ms": (elapsed / len(items)) * 1000 if items else 0,
            "results": results,
        }

    def fetch_prices_parallel(self, mints: List[str], fetch_func: Callable) -> Dict:
        """Fetch múltiplos preços em paralelo."""
        futures = {}
        prices = {}

        # Submit all fetch jobs
        for mint in mints:
            future = self.executor.submit(fetch_func, mint)
            futures[future] = mint

        # Aguarda com timeout
        for future in as_completed(futures, timeout=30):
            mint = futures[future]
            try:
                price = future.result()
                if price:
                    with self.lock:
                        prices[mint] = price
            except Exception as e:
                with self.lock:
                    self.errors.append({"mint": mint, "error": str(e)})

        return {
            "fetched": len(prices),
            "total": len(mints),
            "prices": prices,
            "errors": len(self.errors),
        }

    def shutdown(self):
        """Encerra executor."""
        self.executor.shutdown(wait=True)

class BatchProcessor:
    """Processa dados em batches otimizados."""

    def __init__(self, batch_size: int = 100):
        self.batch_size = batch_size

    def process_batches(self, items: List[Any], process_func: Callable) -> List:
        """Processa items em batches."""
        results = []

        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            batch_result = process_func(batch)
            results.extend(batch_result)

        return results

    def process_batches_parallel(
        self, items: List[Any], process_func: Callable, max_workers: int = 4
    ) -> List:
        """Processa batches em paralelo."""
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = []

        # Submit batches
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            future = executor.submit(process_func, batch)
            futures.append(future)

        # Aguarda e coleta resultados
        results = []
        for future in as_completed(futures):
            try:
                batch_result = future.result()
                results.extend(batch_result)
            except Exception as e:
                print(f"⚠️  Batch error: {e}")

        executor.shutdown(wait=True)
        return results

# ─────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────

def parallel_map(func: Callable, items: List[Any], max_workers: int = 4) -> List:
    """Map function over items em paralelo."""
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = [executor.submit(func, item) for item in items]

    results = []
    for future in as_completed(futures):
        try:
            results.append(future.result())
        except Exception as e:
            print(f"⚠️  Error: {e}")

    executor.shutdown(wait=True)
    return results

def parallel_filter(
    func: Callable, items: List[Any], max_workers: int = 4
) -> List:
    """Filter items em paralelo (keep if func returns True)."""
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {executor.submit(func, item): item for item in items}

    results = []
    for future in as_completed(futures):
        item = futures[future]
        try:
            if future.result():
                results.append(item)
        except Exception as e:
            print(f"⚠️  Error filtering {item}: {e}")

    executor.shutdown(wait=True)
    return results

# ─────────────────────────────────────────────────────────
# EXEMPLO DE USO
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("⚡ Parallel Executor — Phase 3")
    print("="*60)

    # Função de teste
    def slow_operation(x):
        time.sleep(0.1)
        return x * 2

    # Teste 1: Parallel map
    print("\n✓ Testing parallel map...")
    items = list(range(10))
    start = time.time()
    results = parallel_map(slow_operation, items, max_workers=4)
    elapsed = time.time() - start

    print(f"  Items: {len(items)}")
    print(f"  Time: {elapsed:.2f}s (vs {len(items)*0.1:.2f}s sequential)")
    print(f"  Speedup: {len(items)*0.1/elapsed:.1f}x")

    # Teste 2: Batch processing
    print("\n✓ Testing batch processor...")
    processor = BatchProcessor(batch_size=5)

    def process_batch(batch):
        return [x * 2 for x in batch]

    results = processor.process_batches_parallel(items, process_batch, max_workers=2)
    print(f"  Processed {len(results)} items")

    # Teste 3: ParallelAnalyzer
    print("\n✓ Testing ParallelAnalyzer...")
    analyzer = ParallelAnalyzer(max_workers=3)

    def analyze_item(x):
        time.sleep(0.05)
        return {"item": x, "analyzed": x * 3}

    stats = analyzer.analyze_batch(items, analyze_item)
    print(f"  Completed: {stats['completed']}/{stats['total']}")
    print(f"  Time: {stats['elapsed_sec']:.2f}s")
    print(f"  Avg: {stats['avg_per_item_ms']:.1f}ms/item")

    analyzer.shutdown()

    print("\n✅ Parallel executor ready!")
