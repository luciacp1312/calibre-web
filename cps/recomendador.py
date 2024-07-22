import numpy as np
import random


# Preguntas y géneros
questions = {
    1: '¿Te gusta el café?',
    2: '¿Eres una persona misteriosa?',
    3: '¿Eres extrovertid@?',
    4: '¿Te consideras tont@?',
}

genres = [
    {'name': 'Ciencia ficción', 'answers': {1: 1, 2: 1, 3: 1, 4: 0}},
    {'name': 'Novela romántica', 'answers': {1: 1, 2: 1, 3: 1, 4: 0.25}},
    {'name': 'Cómic', 'answers': {1: 0, 2: 0, 3: 0}},
]

questions_so_far = []
answers_so_far = []

# Funciones de cálculo
def calculate_probabilities(questions_so_far, answers_so_far):
    probabilities = []
    for character in genres:
        probabilities.append({
            'name': character['name'],
            'probability': calculate_character_probability(character, questions_so_far, answers_so_far)
        })
    return probabilities

def calculate_character_probability(character, questions_so_far, answers_so_far):
    P_character = 1 / len(genres)
    P_answers_given_character = 1
    P_answers_given_not_character = 1
    for question, answer in zip(questions_so_far, answers_so_far):
        P_answers_given_character *= max(1 - abs(answer - character_answer(character, question)), 0.01)
        P_answer_not_character = np.mean([1 - abs(answer - character_answer(not_character, question))
                                          for not_character in genres
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