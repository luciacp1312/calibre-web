import numpy as np
import random

# Preguntas y recomendaciones
questions = {
    1: {
        'text': '1. ¿Qué género literario prefieres?',
        'options': {
            1: 'Ciencia ficción',
            2: 'Romance',
            3: 'Fantasía',
            4: 'Misterio'
        }
    },
    2: {
        'text': '2. ¿Cuál es tu ritmo de lectura preferido?',
        'options': {
            1: 'Rápido y emocionante',
            2: 'Lento y reflexivo',
            3: 'Moderado y constante',
            4: 'Variable según la historia'
        }
    },
    3: {
        'text': '3. ¿Qué tipo de protagonistas prefieres?',
        'options': {
            1: 'Héroes valientes',
            2: 'Personas comunes en situaciones extraordinarias',
            3: 'Personajes complejos y enigmáticos',
            4: 'Detectives o investigadores'
        }
    },
    4: {
        'text': '4. ¿Qué ambiente prefieres en tus lecturas?',
        'options': {
            1: 'Futurista',
            2: 'Histórico',
            3: 'Mágico',
            4: 'Urbano'
        }
    },
    5: {
        'text': '5. ¿Te gustan los finales?',
        'options': {
            1: 'Felices',
            2: 'Abiertos',
            3: 'Sorprendentes',
            4: 'Agridulces'
        }
    },
    6: {
        'text': '6. ¿Cuánto te importa la construcción del mundo en la historia?',
        'options': {
            1: 'Mucho, debe ser detallada',
            2: 'Algo, pero no es lo más importante',
            3: 'Poco, prefiero enfocarme en los personajes',
            4: 'Nada, mientras la historia sea buena'
        }
    },
    7: {
        'text': '7. ¿Qué prefieres en una historia?',
        'options': {
            1: 'Acción constante',
            2: 'Desarrollo profundo de personajes',
            3: 'Misterios por resolver',
            4: 'Romance y relaciones personales'
        }
    },
    8: {
        'text': '8. ¿Te gustan las series de libros?',
        'options': {
            1: 'Sí, prefiero que la historia continúe',
            2: 'No, prefiero libros autoconclusivos',
            3: 'Depende de la historia',
            4: 'Prefiero trilogías o sagas cortas'
        }
    },
    9: {
        'text': '9. ¿Qué longitud de libros prefieres?',
        'options': {
            1: 'Libros cortos (menos de 300 páginas)',
            2: 'Libros medianos (300-500 páginas)',
            3: 'Libros largos (más de 500 páginas)',
            4: 'No tengo preferencia'
        }
    },
    10: {
        'text': '10. ¿Qué tono prefieres en los libros?',
        'options': {
            1: 'Optimista',
            2: 'Realista',
            3: 'Oscuro',
            4: 'Satírico o humorístico'
        }
    },
}


books = [
    {'name': 'Dune 1, El marciano Andy Weir, Fundación 1: Fundación Isaac Asimov', 'answers': {1: 1, 2: 1, 3: 1, 4: 1, 5: 3, 6: 1, 7: 1, 8: 1, 9: 3, 10: 2}},
    {'name': 'Orgullo y prejuicio, Bajo la misma estrella, Cometas en el cielo', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 1, 6: 2, 7: 4, 8: 2, 9: 2, 10: 1}},
    {'name': 'Harry Potter and the Sorcerers Stone J.K. Rowling, El nombre del viento Patrick Rothfuss, El asesinato de Roger Ackroyd', 'answers': {1: 3, 2: 3, 3: 1, 4: 3, 5: 4, 6: 1, 7: 3, 8: 3, 9: 3, 10: 3}},
    {'name': 'La chica del tren, El código Da Vinci Dan Brown libro, Perdida Gillian Flynn', 'answers': {1: 4, 2: 1, 3: 3, 4: 4, 5: 3, 6: 2, 7: 3, 8: 2, 9: 2, 10: 3}},
    {'name': 'Cien años de soledad, Crimen y Castigo, To Kill a Mockingbird', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 2, 7: 2, 8: 2, 9: 3, 10: 2}},
    {'name': 'Maus Art Spiegelman libro, Watchmen, Sandman 01', 'answers': {1: 4, 2: 1, 3: 4, 4: 4, 5: 2, 6: 2, 7: 1, 8: 2, 9: 1, 10: 4}},
    {'name': 'El señor de los anillos 1, Juego de tronos 1, La rueda del tiempo: El ojo del mundo', 'answers': {1: 3, 2: 3, 3: 1, 4: 3, 5: 3, 6: 1, 7: 2, 8: 1, 9: 3, 10: 2}},
    {'name': 'Sapiens: De animales a dioses, Diario de Ana Frank, Mans search for meaning', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 1, 7: 3, 8: 2, 9: 2, 10: 2}},
    {'name': 'Normal People, La casa de los espíritus, La sombra del viento', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 2, 7: 2, 8: 2, 9: 2, 10: 3}},
    {'name': '1984 George Orwell, El cuento de la criada Margaret Atwood, Un Mundo Feliz Aldous Huxley', 'answers': {1: 1, 2: 1, 3: 1, 4: 1, 5: 4, 6: 2, 7: 3, 8: 2, 9: 2, 10: 3}},
]

questions_so_far = []
answers_so_far = []

# Funciones de cálculo
def calculate_probabilities(questions_so_far, answers_so_far):
    probabilities = []
    for book in books:
        probabilities.append({
            'name': book['name'],
            'probability': calculate_book_probability(book, questions_so_far, answers_so_far)
        })
    return probabilities

def calculate_book_probability(book, questions_so_far, answers_so_far):
    P_book = 1 / len(books)
    P_answers_given_book = 1
    P_answers_given_not_book = 1
    for question, answer in zip(questions_so_far, answers_so_far):
        P_answers_given_book *= max(1 - abs(answer - book_answer(book, question)), 0.01)
        P_answer_not_book = np.mean([1 - abs(answer - book_answer(not_book, question))
                                          for not_book in books
                                          if not_book['name'] != book['name']])
        P_answers_given_not_book *= max(P_answer_not_book, 0.01)
    P_answers = P_book * P_answers_given_book + (1 - P_book) * P_answers_given_not_book
    P_book_given_answers = (P_answers_given_book * P_book) / P_answers
    return P_book_given_answers

def book_answer(book, question):
    if question in book['answers']:
        return book['answers'][question]
    return 0.5

def pregunta(question, answer):
    global questions_so_far, answers_so_far

    if question and answer:
        questions_so_far.append(int(question))
        answers_so_far.append(float(answer))

    probabilities = calculate_probabilities(questions_so_far, answers_so_far)

    questions_left = list(set(questions.keys()) - set(questions_so_far))
    if len(questions_left) == 0:
        result = sorted(probabilities, key=lambda p: p['probability'], reverse=True)[0]
        questions_so_far = []
        answers_so_far = []
        return None, None, result['name']
    else:
        next_question = random.choice(questions_left)
        return next_question, questions[next_question], None