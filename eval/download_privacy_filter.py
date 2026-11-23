"""One-off download of the OpenAI Privacy Filter (Apache 2.0) ONNX int8 build.

Run once:  .\\.venv\\Scripts\\python.exe -m eval.download_privacy_filter
Files go to models/privacy-filter/ (git-ignored). Nothing here is sent anywhere except
the download requests to huggingface.co.
"""
import sys
from pathlib import Path

import httpx

BASE = 'https://huggingface.co/openai/privacy-filter/resolve/main/'
FILES = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'viterbi_calibration.json',
         'onnx/model_quantized.onnx', 'onnx/model_quantized.onnx_data']
TARGET = Path(__file__).resolve().parent.parent / 'models' / 'privacy-filter'


def download(name):
    dest = TARGET / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream('GET', BASE + name, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get('content-length', 0))
        if dest.exists() and total and dest.stat().st_size == total:
            print(f'have {name}')
            return
        tmp = dest.with_suffix(dest.suffix + '.part')
        done = 0
        with open(tmp, 'wb') as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and done % (200 << 20) < (1 << 20):
                    print(f'  {name}: {done >> 20} / {total >> 20} MB', flush=True)
        tmp.replace(dest)
    print(f'done {name}', flush=True)


if __name__ == '__main__':
    for file_name in FILES:
        download(file_name)
    size = sum(p.stat().st_size for p in TARGET.rglob('*') if p.is_file())
    print(f'total {size / 1e9:.2f} GB in {TARGET}')
    sys.exit(0)
