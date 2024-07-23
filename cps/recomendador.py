import numpy as np
import random


# Preguntas y recomendaciones
questions = {
    1: '¿Qué género literario prefieres? (1. Ciencia ficción, 2. Romance, 3. Fantasía, 4. Misterio)',
    2: '¿Cuál es tu ritmo de lectura preferido? (1. Rápido y emocionante, 2. Lento y reflexivo, 3. Moderado y constante, 4. Variable según la historia)',
    3: '¿Qué tipo de protagonistas prefieres? (1. Héroes valientes, 2. Personas comunes en situaciones extraordinarias, 3. Personajes complejos y enigmáticos, 4. Detectives o investigadores)',
    4: '¿Qué ambiente prefieres en tus lecturas? (1. Futurista, 2. Histórico, 3. Mágico, 4. Urbano)',
    5: '¿Te gustan los finales? (1. Felices, 2. Abiertos, 3. Sorprendentes, 4. Agridulces)',
    6: '¿Cuánto te importa la construcción del mundo en la historia? (1. Mucho, debe ser detallada, 2. Algo, pero no es lo más importante, 3. Poco, prefiero enfocarme en los personajes, 4. Nada, mientras la historia sea buena)',
    7: '¿Qué prefieres en una historia? (1. Acción constante, 2. Desarrollo profundo de personajes, 3. Misterios por resolver, 4. Romance y relaciones personales)',
    8: '¿Te gustan las series de libros? (1. Sí, prefiero que la historia continúe, 2. No, prefiero libros autoconclusivos, 3. Depende de la historia, 4. Prefiero trilogías o sagas cortas)',
    9: '¿Qué longitud de libros prefieres? (1. Libros cortos (menos de 300 páginas), 2. Libros medianos (300-500 páginas), 3. Libros largos (más de 500 páginas), 4. No tengo preferencia)',
    10: '¿Qué tono prefieres en los libros? (1. Optimista, 2. Realista, 3. Oscuro, 4. Satírico o humorístico)',
}

books = [
    {'name': 'Dune, Neuromante y Fundación', 'answers': {1: 1, 2: 1, 3: 1, 4: 1, 5: 3, 6: 1, 7: 1, 8: 1, 9: 3, 10: 2}},
    {'name': 'Orgullo y prejuicio, Bajo la misma estrella y Cometas en el cielo', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 1, 6: 2, 7: 4, 8: 2, 9: 2, 10: 1}},
    {'name': 'Harry Potter, El nombre del viento y El asesinato de Roger Ackroyd', 'answers': {1: 3, 2: 3, 3: 1, 4: 3, 5: 4, 6: 1, 7: 3, 8: 3, 9: 3, 10: 3}},
    {'name': 'La chica del tren, El código Da Vinci y Perdida', 'answers': {1: 4, 2: 1, 3: 3, 4: 4, 5: 3, 6: 2, 7: 3, 8: 2, 9: 2, 10: 3}},
    {'name': 'Cien años de soledad, Crimen y castigo y Matar a un ruiseñor', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 2, 7: 2, 8: 2, 9: 3, 10: 2}},
    {'name': 'Maus, Watchmen y Sandman', 'answers': {1: 4, 2: 1, 3: 4, 4: 4, 5: 2, 6: 2, 7: 1, 8: 2, 9: 1, 10: 4}},
    {'name': 'El señor de los anillos, Juego de tronos y La rueda del tiempo', 'answers': {1: 3, 2: 3, 3: 1, 4: 3, 5: 3, 6: 1, 7: 2, 8: 1, 9: 3, 10: 2}},
    {'name': 'Sapiens, El diario de Ana Frank y El hombre en busca de sentido', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 1, 7: 3, 8: 2, 9: 2, 10: 2}},
    {'name': 'Normal People, La casa de los espíritus y La sombra del viento', 'answers': {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 2, 7: 2, 8: 2, 9: 2, 10: 3}},
    {'name': '1984, El cuento de la criada y Un mundo feliz', 'answers': {1: 1, 2: 1, 3: 1, 4: 1, 5: 4, 6: 2, 7: 3, 8: 2, 9: 2, 10: 3}},
]


questions_so_far = []
answers_so_far = []

# Funciones de cálculo
def calculate_probabilities(questions_so_far, answers_so_far):
    probabilities = []
    for character in books:
        probabilities.append({
            'name': character['name'],
            'probability': calculate_character_probability(character, questions_so_far, answers_so_far)
        })
    return probabilities

def calculate_character_probability(character, questions_so_far, answers_so_far):
    P_character = 1 / len(books)
    P_answers_given_character = 1
    P_answers_given_not_character = 1
    for question, answer in zip(questions_so_far, answers_so_far):
        P_answers_given_character *= max(1 - abs(answer - character_answer(character, question)), 0.01)
        P_answer_not_character = np.mean([1 - abs(answer - character_answer(not_character, question))
                                          for not_character in books
                                          if not_character['name'] != character['name']])
        P_answers_given_not_character *= max(P_answer_not_character, 0.01)
    P_answers = P_character * P_answers_given_character + (1 - P_character) * P_answers_given_not_character
    P_character_given_answers = (P_answers_given_character * P_character) / P_answers
    return P_character_given_answers

def character_answer(character, question):
    if question in character['answers']:
        return character['answers'][question]
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
        questions_so_far = [] # nuevo
        answers_so_far = [] # nuevo
        return None, None, result['name']
    else:
        next_question = random.choice(questions_left)
        return next_question, questions[next_question], None

'''# Ruta del recomendador
@app.route('/recomendador', methods=['GET', 'POST'])
def recomendador():
    global questions_so_far, answers_so_far

    if request.method == 'POST':
        question = request.form.get('question')
        answer = request.form.get('answer')
        next_question, question_text, result = pregunta(question, answer)
        if result:
            questions_so_far = []
            answers_so_far = []
            return render_template('recomendador.html', result=result)
        else:
            return render_template('recomendador.html', question=next_question, question_text=question_text)
    return render_template('recomendador.html', question=1, question_text=questions[1])
'''