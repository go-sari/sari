import json
from pathlib import Path

from prodict import Prodict

from main.updater import Updater

model_json = Path("model.json").read_text()
model = Prodict.from_dict(json.loads(model_json))

Updater(model).update_all()
