import tornado
from tornado.web import Application, RequestHandler

import os
from datetime import datetime
import uuid

request_queue = None
response_queue = None
tensorboard_address = None
config = None

ui = []
response_history = []


class MainHandler(RequestHandler):
    async def get(self):
        await self.render("index.html", context={
            'experiment_name': config['experiment_name'],
            'config': {k:v for k,v in config.items() if k != 'experiment_name'},
            'tensorboard_address': tensorboard_address,
            'controls': [c.to_dict() for c in ui],

        }, responses=response_history)

    def post(self):
        request_queue.put({k:v for k,v in self.request.arguments.items() if k != '_xsrf'})
        response = response_queue.get()
        response_history.append({
            'time': datetime.now().strftime('%d %b %Y, %H:%M'),
            'content': response,
            '_uuid': str(uuid.uuid4())
        })
        self.write({'responses': response_history})


def _target(port, ip, config_, ui_, request_queue_, response_queue_):
    app = Application([
        (r"/", MainHandler)
    ],
        cookie_secret=42,
        template_path=os.path.join(os.path.dirname(__file__), "templates"),
        static_path=os.path.join(os.path.dirname(__file__), "static"),
        xsrf_cookies=True,
        debug=True,
    )

    global tensorboard_address
    global request_queue
    global response_queue
    global config
    global ui

    tensorboard_address = 'http://' + ip + ':' + str(port - 1)
    request_queue = request_queue_
    response_queue = response_queue_
    config = config_
    ui = ui_

    app.listen(port)
    tornado.ioloop.IOLoop.current().start()
