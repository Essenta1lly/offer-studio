"""Install the adapter and shared modules into this clean Cardinal tree."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
source=root/'OfferStudio'/'Server'
cardinal=root/'Cardinal'
code=cardinal/'storage'/'offer_studio'/'code'
code.mkdir(parents=True,exist_ok=True)
(cardinal/'plugins').mkdir(exist_ok=True)
for name in ('core.py','operations.py','batch.py','creation.py','worker.py','ai_assistant.py','ai_instructions.txt','backup.py'):
    shutil.copy2(source/name,code/name)
shutil.copy2(source/'offer_studio_plugin.py',cardinal/'plugins'/'offer_studio.py')
print('Adapter installed. Runtime data is excluded by .gitignore.')
