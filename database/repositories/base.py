from abc import ABC, abstractmethod


class Repository(ABC):

    @abstractmethod
    def save(self, data):
        pass