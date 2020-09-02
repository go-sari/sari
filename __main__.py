from pathlib import Path

import bson
from prodict import Prodict

from main.updater import Updater

model_json = Path("model.json").read_bytes()
model = Prodict.from_dict(bson.loads(model_json))

Updater(model).update_all()
