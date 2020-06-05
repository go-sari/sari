import abc
from typing import List, Tuple

from prodict import Prodict

from main.issue import Issue


class Gatherer:
    @abc.abstractmethod
    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        pass
