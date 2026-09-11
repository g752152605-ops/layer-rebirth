import subprocess
import sys

from run_app import NativeApi


def test_native_bridge_does_not_traverse_window():
    api = NativeApi()
    api._window = object()
    # pywebview recursively reads every public attribute during bridge injection.
    public = {name: getattr(api, name) for name in dir(api) if not name.startswith('_')}
    assert set(public) == {'choose_images', 'choose_export_directory'}
    assert all(callable(value) for value in public.values())


def test_server_startup_keeps_processing_libraries_unloaded(tmp_path):
    script = '''
import sys
from pathlib import Path
from layer_rebirth.server import create_server
server = create_server('127.0.0.1', 0, Path('web'), Path(sys.argv[1]))
try:
    assert server.worker._process is None
    assert not {'matplotlib', 'cv2', 'vtracer', 'rapidocr'} & sys.modules.keys()
finally:
    server.server_close()
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True, timeout=20)
