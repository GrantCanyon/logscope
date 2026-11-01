"""Общее русское оформление справки и ошибок параметров вспомогательных скриптов."""

import argparse
import sys


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, add_help=False, **kwargs)
        self.add_argument(
            "-h", "--help", action="help", help="Показать справку и завершить работу"
        )
        self._positionals.title = "Позиционные параметры"
        self._optionals.title = "Параметры"

    def format_usage(self):
        return super().format_usage().replace("usage:", "Использование:", 1)

    def format_help(self):
        return super().format_help().replace("usage:", "Использование:", 1)

    def error(self, message):
        translations = {
            "the following arguments are required:": "не заданы обязательные параметры:",
            "unrecognized arguments:": "неизвестные параметры:",
            "expected one argument": "ожидается одно значение",
            "invalid int value:": "недопустимое целое число:",
            "argument ": "параметр ",
        }
        for source, target in translations.items():
            message = message.replace(source, target)
        self.print_usage(sys.stderr)
        self.exit(2, f"Ошибка параметров: {message}\n")
