class ControlProto:
    name: str
    callback: callable


class Button:
    def __init__(self, name, description, callback):
        self.name = name
        self.description = description
        self.callback = callback

    def to_dict(self):
        return {
            'type': 'button',
            'name': self.name,
            'description': self.description
        }


class Field:
    def __init__(self, name, description, callback, placeholder_text=''):
        self.name = name
        self.description = description
        self.placeholder_text = placeholder_text
        self.callback = callback

    def to_dict(self):
        return {
            'type': 'field',
            'name': self.name,
            'description': self.description,
            'placeholder_text': self.placeholder_text
        }