
import json
from lib2to3.pgen2.token import NOTEQUAL
from os.path import join
from typing import List

from sklearn.metrics import precision_recall_fscore_support, f1_score

from ee_data import EEDataloader
import openai
import os
import random
import time
import re
import argparse
from collections import Counter


entity_type_dict = {'疾病': 'dis', '临床表现': 'sym', '医疗程序': 'pro', '医疗设备': 'equ', '药物': 'dru', '医学检验项目': 'ite',
                    '身体': 'bod', '科室': 'dep', '微生物类': 'mic'}

reverse_entity_type_dict = {'dis': '疾病', 'sym': '临床表现', 'pro': '医疗程序', 'equ': '医疗设备', 'dru': '药物', 'ite': '医学检验项目',
                            'bod': '身体', 'dep': '科室', 'mic': '微生物类'}


def get_completion(mess, model='gpt-3.5-turbo', max_tokens=1024, temperature=0.5):
    completion = openai.ChatCompletion.create(
        model=model,
        messages=mess,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=None,
        n=1,
    )
    return completion


def get_examples(num_examples, cblue_root='../data/CBLUEDatasets'):

    example_list = []
    for i in range(num_examples):

        examples = EEDataloader(cblue_root, augment=False).get_data("train")

        idx = random.randint(0, len(examples) - 1)
        text = examples[idx].text
        entity_list = examples[idx].entities
        prompt_text = f"文本：\n {text} \n"

        for _, entity_dict in enumerate(entity_list):
            start_idx = entity_dict['start_idx']
            end_idx = entity_dict['end_idx']
            entity_type = reverse_entity_type_dict[entity_dict['type']]
            entity = entity_dict['entity']
            prompt_entity = f"|实体类别|实体名称|实体起始位置|实体结束位置|\n|-----|-----|-----|-----|\n|{entity_type}|{entity}|{start_idx}|{end_idx}|\n"
            prompt_text += prompt_entity

        example_list.append(prompt_text)

    return example_list


def predict_ner(openai_key, sentence, examples, use_index=False, model='gpt-3.5-turbo', max_tokens=1024, temperature=0.5,
                ent_type_dic=entity_type_dict):
    openai.api_key = openai_key
    # openai.api_base = "https://closeai.deno.dev/v1"

    examples_str = "\n".join(examples)
    mess = [{"role": "system", "content": f"你是一个中文医疗信息处理的助手，你要完成命名实体识别的任务，根据给定的句子，识别出其中的实体，如果不存在则回答：无\n \
             按照表格形式回复，表格有四列且表头为（实体类型，实体名称，实体起始位置，实体结束位置）（注：位置从零开始计数）。给定实体类型列表：{ent_type_dic.keys()}。"},
            {"role": "assistant", "content": examples_str}, {"role": "user", "content": sentence}]

    try:
        completion = get_completion(mess, model, max_tokens, temperature)
    except openai.error.RateLimitError:
        time.sleep(20)
        completion = get_completion(mess, model, max_tokens, temperature)

    output_str = completion.choices[0].message.content.strip()
    entities = []

    res = re.findall(r"\|.*?\|.*?\|.*?\|.*?\|", output_str)
    count = 0
    if not use_index:
        exist_entities = []
    for so in res:
        count += 1
        if count <= 2:
            continue
        so = so[1:-1].split("|")

        if len(so) == 4:
            s, o, on, off = so
            # print(o, s, on, off)
            try:
                if use_index:
                    entities.append({'entity': o, 'type': entity_type_dict[s], 'start_idx': int(on), 'end_idx': int(off)})
                else:
                    if o in exist_entities:
                        continue
                    exist_entities.append(o)
                    for match in re.finditer(o, sentence):
                        start_idx = match.start()
                        end_idx = match.end() - 1
                        entities.append({'entity': o, 'type': entity_type_dict[s], 'start_idx': start_idx, 'end_idx': end_idx})

            except:
                continue

    return entities


def self_consistency_prompt(num_examples, openai_key, sentence, consistency_number=20, threshold=5):
    entity_counter = dict()
    consistency_dict = dict()
    for i in range(consistency_number):
        examples = get_examples(num_examples=num_examples)
        try:
            output_entities = predict_ner(openai_key, sentence, examples)
        except openai.error.RateLimitError:
            time.sleep(20)
            output_entities = predict_ner(openai_key, sentence, examples)

        for entity in output_entities:
            entity_str = json.dumps(entity, ensure_ascii=False)

            if entity_str in entity_counter:
                entity_counter[entity_str] += 1
            else:
                entity_counter[entity_str] = 1
        print(entity_counter)

    consistency_dict['text'] = sentence
    consistency_dict['entities'] = [json.loads(key) for key, value in entity_counter.items() if value >= threshold]
    print(consistency_dict)

    return consistency_dict


def generate_test_results(args):
    test_examples = EEDataloader(args.root, augment=False).get_data("select_dev")[args.start: args.start + args.length]

    if args.continue_generate:
        with open(os.path.join(args.output_dir, args.output_file), 'r', encoding="utf8") as f:
            exist_answer = json.load(f)
    else:
        exist_answer = []

    predictions = []

    for i, example in enumerate(test_examples):
        texts = example.text
        try:
            consistency_dict = self_consistency_prompt(openai_key=args.openai_key, num_examples=args.num_examples,
                                                sentence=texts, consistency_number=args.num_consistency, threshold=args.threshold)
        except openai.error.InvalidRequestError:
            time.sleep(20)
            consistency_dict = self_consistency_prompt(openai_key=args.openai_key, num_examples=args.num_examples,
                                                sentence=texts, consistency_number=args.num_consistency, threshold=args.threshold)

        predictions.append(consistency_dict)

    final_answer = exist_answer + predictions

    with open(join(args.output_dir, args.output_file), "w", encoding="utf8") as f:
        json.dump(final_answer, f, indent=2, ensure_ascii=False)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Arguments about gpt model and prompting method')
    # openai.api_base = "https://api.chatanywhere.com.cn/v1"
    parser.add_argument('-c', '--continue_generate', help='continue generate', action='store_true')
    parser.add_argument('-s', '--start', help='start index', type=int, default=0)
    parser.add_argument('-l', '--length', help='the number of texts', type=int, default=5)
    parser.add_argument('-k', '--openai_key', help='openai key', type=str, default='sk-vfeBBt7u8EmMNX6NbTrTtmMOasS7GxV9Clgzctf4nfYzn0R0')
    parser.add_argument('-m', '--model', help='openai model', type=str, default='gpt-3.5-turbo')
    parser.add_argument('-t', '--temperature', help='openai temperature', type=float, default=0.5)
    parser.add_argument('-i', '--use_index', help='use the indexes from the output of gpt model', action='store_true')
    parser.add_argument('-n', '--num_examples', help='number of examples', type=int, default=3)
    parser.add_argument('-nc', '--num_consistency', help='the number of different promptings in the self-consistency method', type=int, default=5)
    parser.add_argument('-r', '--root', help='cblue root', type=str, default='../data/CBLUEDatasets')
    parser.add_argument('-th', '--threshold', help='the threshold number of the consistency', type=int, default=2)
    parser.add_argument('-o', '--output_dir', help='output dir', type=str, default='../ckpts/chatgpt_api')
    parser.add_argument('-f', '--output_file', help='the name of the output file', type=str, default='CMeEE_consistency.json')
    args = parser.parse_args()
    while args.start + args.length <= 500:
        generate_test_results(args)
        args.start = args.start + args.length