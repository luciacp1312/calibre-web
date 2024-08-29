import unittest
from flask_testing import TestCase
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from cps import app, db
from cps.ub import User, Message, Notification

# Crea un nuevo Base para la base de datos de pruebas para evitar conflictos
TestBase = declarative_base()

class AppTestCase(TestCase):


    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine('sqlite:///test.db')  # Path to your test DB
        TestBase.metadata.create_all(cls.engine)  # Create tables
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        # Crear una sesión para la base de datos
        self.session = self.Session()

        # Crear usuarios para las pruebas
        self.user1 = User(name="User1", email="user1@example.com", password="password")
        self.user2 = User(name="User2", email="user2@example.com", password="password")
        self.session.add_all([self.user1, self.user2])
        self.session.commit()

    def tearDown(self):
        # Limpiar la base de datos después de cada prueba
        self.session.remove()  # Asegúrate de usar remove() si usas scoped_session
        TestBase.metadata.drop_all(self.engine)  # Eliminar tablas en la base de datos de pruebas

    def test_chat_post_message(self):
        with self.client:
            # Loguear al usuario 1
            self.client.post('/login', data=dict(email="user1@example.com", password="password"))
            
            # Enviar un mensaje desde user1 a user2
            response = self.client.post(f'/chat/{self.user2.id}', data=dict(content="Hola User2"))
            self.assertEqual(response.status_code, 302)  # Redirigido tras enviar el mensaje

            # Verificar que el mensaje fue guardado en la base de datos
            message = Message.query.filter_by(sender_id=self.user1.id, receiver_id=self.user2.id).first()
            self.assertIsNotNone(message)
            self.assertEqual(message.content, "Hola User2")

    def test_delete_message(self):
        with self.client:
            # Crear un mensaje para eliminar
            message = Message(sender_id=self.user1.id, receiver_id=self.user2.id, content="Mensaje a eliminar")
            self.session.add(message)
            self.session.commit()

            # Loguear al usuario 1
            self.client.post('/login', data=dict(email="user1@example.com", password="password"))

            # Eliminar el mensaje
            response = self.client.post(f'/delete_message/{message.id}')
            self.assertEqual(response.status_code, 302)  # Redirigido tras eliminar el mensaje

            # Verificar que el mensaje fue eliminado
            message = Message.query.get(message.id)
            self.assertIsNone(message)

    def test_follow_user(self):
        with self.client:
            # Loguear al usuario 1
            self.client.post('/login', data=dict(email="user1@example.com", password="password"))

            # Seguir al usuario 2
            response = self.client.get(f'/follow/{self.user2.name}')
            self.assertEqual(response.status_code, 302)  # Redirigido tras seguir al usuario

            # Verificar que user1 sigue a user2
            self.assertTrue(self.user1.is_following(self.user2))

    def test_notifications(self):
        with self.client:
            # Crear una notificación para user1
            notification = Notification(user_id=self.user1.id, message="Test notification")
            self.session.add(notification)
            self.session.commit()

            # Loguear al usuario 1
            self.client.post('/login', data=dict(email="user1@example.com", password="password"))

            # Verificar las notificaciones
            response = self.client.get('/notifications')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Test notification", response.data)

if __name__ == '__main__':
    unittest.main()
