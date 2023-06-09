"""
Feature processing:
    Default:
        data_folder='data/processed'
        features_folder='data/features'
        config_folder='cnf'

    Config (config_folder):
        - 'professions.json': professions json

    Input (data_folder):
        - 'vacancies.csv': Dataframe

    Output (features_folder):
        - 'skills.txt': all skills after corrections ordered by name
        - 'skill_index_to_corrected.pkl': dictionary skill index to corrected skill name
        - 'skill_original_to_index.pkl': dictionary skill name to index
        - 'prof_index_to_prof_name.pkl': dictinary prof index to prof name
        - 'skills.csv': dataframe with columns:
                ['skill_name', 'skill_id', 'salary_q25', 'salary_q50', 'salary_q75', 'frequency', 
                    'popular_profession_id', 'popular_profession_name', <professions>]
        - 'prof.csv': dataframe with columns:
                ['prof_name', 'prof_id', 'salary_q25', 'salary_q50', 'salary_q75', 'frequency', 'popular_skills']
        - 'matrix.pkl': skill-profession relationship matrix (numpy.array[skill_id, prof_id])


Exsample of using:
    FeaturesProcessor().process()


"""

import os
import numpy as np
import pandas as pd
from src.utils import config
from src.utils.logger import configurate_logger
from typing import Tuple, Dict, List
import re
from tqdm import tqdm
import pickle
import nltk
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer

import warnings
warnings.filterwarnings("ignore")

log = configurate_logger('FeaturesProcessor')

class FeaturesProcessor:

    def __init__(self, 
                data_folder='data/processed',
                features_folder='data/features',
                config_folder='cnf',
                min_vacancies_for_skill = 10):

        tqdm.pandas()

        # vacancies data frame
        filename = os.path.join(data_folder, 'vacancies.csv')
        if not os.path.isfile(filename):
            raise ValueError(f'File does not exists: {filename}')
        self.df = pd.read_csv(filename, encoding='utf-8')

        # professions
        filename = os.path.join(config_folder, 'professions.json')
        if not os.path.isfile(filename):
            raise ValueError(f'File does not exists: {filename}')
        professions_map : List[Dict[str, str]] = config.load(filename)
        self.professions = pd.DataFrame(columns=['query_name', 'profession'])
        for row in professions_map:
            self.professions = self.professions.append(row, ignore_index=True)

        # skill aliases
        filename = os.path.join(config_folder, 'skill_aliases.json')
        if not os.path.isfile(filename):
            raise ValueError(f'File does not exists: {filename}')
        self.skill_aliases : List[List[str]] = config.load(filename)

        self.features_folder = features_folder
        self.min_vacancies_for_skill = min_vacancies_for_skill


    def _extract_skills(self) -> Dict[str, float]:
        """
            Add skill_set column to self.df that contain set of skills for row
            Returns sill dictionary

            return: Dict[str, float]:
                Skills dictionary. Key is skill. Value is normalized frequency
        """
        log.info('Extracting skills...')
        r = re.compile('\\\\|////|,')
        self.df['skill_set'] = self.df['skills'].apply(
            lambda s : {x.strip(" '") for x in re.split(r, s.strip('[]'))} - {''})
        all_skills = set().union(*self.df.skill_set.to_list())

        skills = {}
        rows_count = self.df.shape[0]
        for skill in tqdm(all_skills):
            skills[skill] = self.df.skill_set.apply(lambda x: skill in x).sum() / rows_count

        return skills

    def _skills_corrections(self, skills: Dict[str, float]) -> Tuple[Dict[int, str], Dict[str, int]]:
        """Skills correction:
            - discarding case, spec-sumbols
            - <some more later... (rule-based, spell-shecking, etc)>
            - choosing the best skill name based frequency of skills in group

            Parameters
            ----------
            skills: Dict[str, float] :
                Dict skill name to frequency

            Returns
            -------
            Tuple[Dict[int, str], Dict[str, int]] :
                Tuple of:
                    - dist if original skill name to index
                    - dict index to corrected skill name
        
        """

        log.info('Correcting skills...')

        def simplify(s):
            for x in self.skill_aliases:
                if s.lower() in [y.lower() for y in x]:
                    s = x[0]
                    break

            return s.lower().replace(' ', '').replace('-', '').replace('/','').replace(':','')

        new_index  = 0
        index_to_corrected = {}
        original_to_index = {}
        simplified_to_index = {}
        for skill in skills.keys():
            simplified_skill = simplify(skill)
            index = simplified_to_index.get(simplified_skill, new_index)
            original_to_index[skill] = index

            if index == new_index:
                simplified_to_index[simplified_skill] = index
                index_to_corrected[index] = skill
                new_index += 1
            else:
                skill_aliases = [k for k, v in original_to_index.items() if v == index]
                if skill not in skill_aliases:
                    skill_aliases += [skill]
                best_name = skill_aliases[np.array([skills[x] for x in skill_aliases]).argmax()]
                index_to_corrected[index] = best_name

        return original_to_index, index_to_corrected

    def _create_skill_df(self, original_to_index: Dict[str, int],
                        index_to_corrected: Dict[int, str]) -> pd.DataFrame:
        """
            Create skills dataframe and calculate skills features.
            Dataframe columns:
                - skill_name: skill name
                - skill_id: id
                - salary_q25: 25 salary quantile 
                - salary_q50: 50 salary quantile 
                - salary_q75: 75 salary quantile 
                - frequency: relative frequency
        """

        log.info('Creating skills data frame...')

        skill_df = pd.DataFrame()
        skill_df['skill_name'] = index_to_corrected.values()
        skill_df['skill_id'] = index_to_corrected.keys()

        salary_q25 = {}
        salary_q50 = {}
        salary_q75 = {}
        skill_freq = {}

        df = self.df[['skill_set', 'salary', 'salary_from', 'salary_to']].copy()
        df['skill_ind_set'] = df['skill_set'].apply(lambda x: set([original_to_index[s] for s in x]))

        for s in skill_df.skill_id:
            s_df = df[df['skill_ind_set'].apply(lambda x: s in x)]
            skill_freq[s] = s_df.shape[0] / df.shape[0]
            s_df = s_df[s_df['salary']]
            if s_df.shape[0] > self.min_vacancies_for_skill:
                salaries = (s_df['salary_from'] + s_df['salary_to']) / 2
                salary_q25[s] = int(salaries.quantile(0.25))
                salary_q50[s] = int(salaries.quantile(0.50))
                salary_q75[s] = int(salaries.quantile(0.75))

        skill_df['salary_q25'] = skill_df['skill_id'].apply(lambda x: salary_q25.get(x, None))
        skill_df['salary_q50'] = skill_df['skill_id'].apply(lambda x: salary_q50.get(x, None))
        skill_df['salary_q75'] = skill_df['skill_id'].apply(lambda x: salary_q75.get(x, None))
        skill_df['frequency'] = skill_df['skill_id'].apply(lambda x: skill_freq.get(x, None))

        log.info('Created skills data frame')

        return skill_df


    def save_skill_df(self) -> None:
        """Save skill dataframe to file
        'skills.csv': dataframe with columns:
                ['skill_name', 'skill_id', 'salary_q25', 'salary_q50', 'salary_q75', 'frequency', 
                    'popular_profession_id', 'popular_profession_name', <professions>]
        """
        filename = os.path.join(self.features_folder, 'skills.csv')
        self.skill_df.to_csv(filename, index=False, encoding='utf-8')


    def update_skill_df(self) -> None:
        """Update skill dataframe. Add weighed most popular profession to each skill"""

        log.info('Updating skill dataframe...')

        prof_frequency = self.prof_df.sort_values(by='prof_id').frequency.to_numpy()
        self.skill_df['popular_profession_id'] = (self.matrix / prof_frequency).argmax(axis=1)
        self.skill_df['popular_profession_name'] = \
            self.skill_df.popular_profession_id.apply(lambda x: self.prof_index_to_prof_name[x])

        matrix_colsum = self.matrix.sum(axis=0)
        for k, v in self.prof_index_to_prof_name.items():
            self.skill_df[v] = self.skill_df['skill_id'].apply(lambda x: self.matrix[x, k] / matrix_colsum[k])

        log.info('Updated skill dataframe...')


    def skills_processing(self) -> None:
        """
        Make skill processing
        Save files:
            - 'skills.txt': all skills after corrections ordered by name
            - 'skill_original_to_index.pkl': dictionary skill name to index
            - 'skill_index_to_corrected.pkl': dictionary skill index to corrected skill name
        """

        skills = self._extract_skills()
        self.skill_original_to_index, self.skill_index_to_corrected = self._skills_corrections(skills)

        filename = os.path.join(self.features_folder, 'skills.txt')
        with open(filename, 'w', encoding='utf-8') as f:
            f.writelines([x + '\n' for x in sorted(self.skill_index_to_corrected.values())])

        filename = os.path.join(self.features_folder, 'skill_original_to_index.pkl')
        with open(filename, 'wb') as f:
            pickle.dump(self.skill_original_to_index, f)

        filename = os.path.join(self.features_folder, 'skill_index_to_corrected.pkl')
        with open(filename, 'wb') as f:
            pickle.dump(self.skill_index_to_corrected, f)

        log.info('All skills count = %s, after correction = %s',
                len(self.skill_original_to_index), len(self.skill_index_to_corrected))

        self.skill_df = self._create_skill_df(self.skill_original_to_index, self.skill_index_to_corrected)


    def save_prof_df(self) -> None:
        """Save skill dataframe to file
        'prof.csv': dataframe with columns:
                ['prof_name', 'prof_id', 'salary_q25', 'salary_q50', 'salary_q75', 'frequency', 'popular_skills']
        """

        filename = os.path.join(self.features_folder, 'prof.csv')
        self.prof_df.to_csv(filename, index=False, encoding='utf-8')


    def update_prof_df(self, top_n: int = 10) -> None:
        """
        Update prof_df
        Add weighed most popular profession to each skill"""

        log.info('Updating prof data frame...')
        self.prof_df['popular_skills'] = self.prof_df['prof_id'].apply(
            lambda i: ', '.join([self.skill_index_to_corrected[x] for x in self.matrix[:,i].argsort()[::-1]][:10]))
        log.info('Updated prof data frame...')

    def update_profset_column(self, xdf: pd.DataFrame) -> pd.DataFrame:
        """Update (improove) 'prof_set' column"""

        def _simplify(s):
                return s.lower(). \
                    replace('"', ' '). \
                    replace(",", ' '). \
                    replace('(', ' '). \
                    replace(')', ' '). \
                    replace('\\', ' '). \
                    replace('-', ' '). \
                    replace('/', ' '). \
                    replace('.', ' ')

        #xdf['lemm_name'] = _lemmatize(xdf)

        def apply_rule_and(df: pd.DataFrame, prof: str, keywords_list: List[str] = None, only_empty: bool = False) -> pd.DataFrame:
            if keywords_list is None:
                keywords_list = [prof]

            for keywords in keywords_list:
                keywords = re.split(' |-', keywords)

                rule = df.apply(lambda x: 
                                all([(kw.lower() in x['name'].lower()) for kw in keywords]) and
                                (only_empty == False or len(x['prof_set_rulebase']) == 0)
                                , axis=1)
                filter = df[rule].index

                df.loc[filter, 'prof_set_rulebase'] = \
                    df.loc[filter, 'prof_set_rulebase'].apply(lambda x: x.union(set([prof])))

            return df

        def apply_rule_kw(df: pd.DataFrame, prof: str, keywords: List[str], only_empty: bool = False) -> pd.DataFrame:
            for kw in keywords:
                kw = f' {kw.lower()} '
                rule = df.apply(lambda x:
                                kw in ' '+_simplify(x['name'])+' ' and
                                (only_empty == False or len(x['prof_set_rulebase']) == 0)
                                , axis=1)
                filter = df[rule].index

                df.loc[filter, 'prof_set_rulebase'] = \
                        df.loc[filter, 'prof_set_rulebase'].apply(lambda x: x.union(set([prof])))

            return df

        xdf['prof_set_rulebase'] = [set([]) for _ in range(len(xdf))]

        # Аналитики
        xdf = apply_rule_and(xdf, 'Системный аналитик')
        xdf = apply_rule_and(xdf, 'Бизнес-аналитик', ['Бизнес-аналитик', 'Business Analyst'])
        xdf = apply_rule_kw(xdf, 'Аналитик BI', ['Аналитик BI', 'BI analyst'], only_empty=True)
        xdf = apply_rule_and(xdf, 'Аналитик данных', ['Аналитик данных', 'Data Analyst'])
        xdf = apply_rule_and(xdf, 'Продуктовый аналитик')
        xdf = apply_rule_and(xdf, 'Аналитик', ['Аналитик', 'Analyst'], only_empty=True)

        # Администратор баз данных
        xdf = apply_rule_and(xdf, 'Администратор баз данных', ['Администратор баз данных', 'Администратор БД'])

        # Инженеры данных
        xdf = apply_rule_and(xdf, 'Инженер данных', ['Инженер данных', 'Data Engineer', 
                        'Дата инженер', 'Data инженер',
                        'Data Architect', 'Hadoop', 'Kafka'
        ])
        xdf = apply_rule_kw(xdf, 'Инженер данных', ['баз данных', 'PostgreSQL', 'MSSQL'], only_empty=True)
        xdf = apply_rule_kw(xdf, 'Инженер данных', ['Hadoop', 'Kafka'])


        # Дата Сайенс
        xdf = apply_rule_and(xdf, 'Data Scientist')

        # ML инженер
        xdf = apply_rule_kw(xdf, 'ML инженер', ['ML', 'ETL', 'MLOps'])

        # Big Data
        xdf = apply_rule_kw(xdf, 'Big Data', ['Big Data', 'Биг Дата', 'DWH', 'Data lake', 'Hadoop', 'Kafka',
                                            'больших данных', 'большие данные'])

        # остатки
        xdf = apply_rule_and(xdf, 'Data Scientist', ['Data Science'])
        xdf = apply_rule_and(xdf, 'Computer Vision', ['Computer Vision', 'CV'])
        xdf = apply_rule_and(xdf, 'NLP', ['Computer Vision', 'NLP', 'Natural Language Processing'])

        processed = xdf[xdf.prof_set_rulebase.apply(lambda x: x != set([]))].shape[0]

        rule = xdf.prof_set_rulebase.apply(lambda x: x == set([]))
        filter = xdf[rule].index
        xdf.loc[filter, 'prof_set_rulebase'] = xdf.loc[filter, 'prof_set']

        #xdf.to_csv('data/prof_ds.csv', index=False, encoding='utf-8')
        xdf['prof_set'] = xdf['prof_set_rulebase']
        xdf = xdf.drop(columns=['prof_set_rulebase'])

        # Total: 3339, processed: 3133
        #print(f'Total: {xdf.shape[0]}, processed: {processed}')

        return xdf


    def professions_processing(self) -> None:
        """Professions preprocess:
            Add column 'prof_set' to self.df

        Save files:
            - 'prof_index_to_prof_name': dict profession index to profession name
            - 'vacancy_profset.csv': data frame [vacancy_id, prof_set]
        """

        log.info('Processing professions...')

        # map query to profession
        prof_map = dict(zip(self.professions.query_name, self.professions.profession))


        # calculate 'prof_set' column
        r = re.compile('\\\\|////|,')
        def query_to_prof_set(s):
            query_set = {x.strip(" '") for x in re.split(r, s.strip('[]'))} - {''}
            prof_set = set([])
            for q in query_set:
                prof_set.add(prof_map.get(q.strip('"'), q))
            return prof_set

        self.df['prof_set'] = self.df['query'].apply(query_to_prof_set)

        # improove prof_set
        self.df = self.update_profset_column(self.df)

        # calculate prof_index_to_prof_name
        self.prof_index_to_prof_name = {}
        all_prof = set().union(*self.df.prof_set.to_list())
        for i, n in enumerate(all_prof):
            self.prof_index_to_prof_name[i] = n

        # save prof_index_to_prof_name
        filename = os.path.join(self.features_folder, 'prof_index_to_prof_name.pkl')
        with open(filename, 'wb') as f:
            pickle.dump(self.prof_index_to_prof_name, f)

        # save prof_set
        filename = os.path.join(self.features_folder, 'vacancy_profset.csv')
        self.df[['vacancy_id', 'prof_set']].to_csv(filename, index=False, encoding='utf-8')

        # create prof_df data frame
        self.prof_df = pd.DataFrame()
        self.prof_df['prof_name'] = self.prof_index_to_prof_name.values()
        self.prof_df['prof_id'] = self.prof_index_to_prof_name.keys()
        
        salary_q25 = {}
        salary_q50 = {}
        salary_q75 = {}
        prof_freq = {}

        for p in self.prof_df.prof_name:
            p_df = self.df[self.df['prof_set'].apply(lambda x: p in x)]            
            prof_freq[p] = p_df.shape[0] / self.df.shape[0]
            p_df = p_df[p_df.salary]
            if p_df.shape[0] > 0:
                salaries = (p_df['salary_from'] + p_df['salary_to']) / 2
                salary_q25[p] = round(salaries.quantile(0.25))
                salary_q50[p] = round(salaries.quantile(0.50))
                salary_q75[p] = round(salaries.quantile(0.75))

        self.prof_df['salary_q25'] = self.prof_df.prof_name.apply(lambda x: salary_q25.get(x, None))
        self.prof_df['salary_q50'] = self.prof_df.prof_name.apply(lambda x: salary_q50.get(x, None))
        self.prof_df['salary_q75'] = self.prof_df.prof_name.apply(lambda x: salary_q75.get(x, None))
        self.prof_df['frequency'] = self.prof_df.prof_name.apply(lambda x: prof_freq.get(x, None))

        log.info('Processed professions')

    def rel_matrix_processing(self) -> np.array:
        """
        Return and save skill-profession relationship matrix
        Matrix dim is about 8000x10 therefore numpy is enough

        Save matrix to 'matrix.pkl' file
        """

        log.info('Creating relationship matrix...')

        prof_to_index = {}
        for k, v in self.prof_index_to_prof_name.items():
            prof_to_index[v] = k
        
        self.matrix = np.zeros((len(self.skill_index_to_corrected), len(self.prof_index_to_prof_name)))
        for row in self.df[['prof_set', 'skill_set']].to_numpy():
            for prof in row[0]:
                prof_id = prof_to_index[prof]
                for skill in row[1]:
                    skill_id = self.skill_original_to_index[skill]
                    self.matrix[skill_id, prof_id] += 1

        filename = os.path.join(self.features_folder, 'matrix.pkl')
        with open(filename, 'wb') as f:
            pickle.dump(self.matrix, f)

        log.info('Created relationship matrix')

        return self.matrix

    def rel_matrix_tfidf_processing(self, column: str) -> np.array:
        """
        Save relationship matrix ased tf-idf of name or description
        bla-bla-bla
        
        Embeddings quality is VERY low
        """

        log.info('Creating relationship matrix (TF-IDF of name)...')

        nltk.download('stopwords')
        stop_words = set(stopwords.words('english')).union(stopwords.words('russian'))

        skill_text = []
        for si in tqdm(self.skill_index_to_corrected.keys()):
            # you can try unique text
            st = ' '.join(self.df[self.df.skill_ind_set.apply(lambda x: si in x)][column])
            skill_text.append(st)

        # you can try CountVectorizer
        vectorizer = TfidfVectorizer(stop_words=list(stop_words))
        X = vectorizer.fit_transform(skill_text)

        filename = f'../data/features/matrix_{column}_tfidf.pkl'
        with open(filename, 'wb') as f:
            # numpy for compatibility (35Mb numpy vs 0.4Mb scr_matrix)
            #  but for descriptins it's 650Mb!
            pickle.dump(X.toarray(), f)  

        log.info('Created relationship matrix (TF-IDF of name)')

        return X

    def rel_matrix_vacancy_processing(self) -> np.array:
        """
        Matrix vacany-skill (not prof-skil)
        Bad idea too :(

        Save matrix to 'matrix_val.pkl' file
        """

        log.info('Creating relationship vacancy matrix...')

        matrix_vac = np.zeros((len(self.skill_index_to_corrected), self.df.shape[0]))
        for vi in self.df.index:
            ss = self.df.loc[vi, 'skill_ind_set']
            for si in ss:
                matrix_vac[si, vi] += 1

        filename = os.path.join(self.features_folder, 'matrix_vac.pkl')
        with open(filename, 'wb') as f:
            pickle.dump(matrix_vac, f)

        log.info('Created relationship vacancy matrix')

        return matrix_vac

    def process(self) -> None:
        """Conduct all process. Input and output date in files"""

        self.skills_processing()
        self.professions_processing()

        self.rel_matrix_processing()
        # tf-ifd не зашло, просто раскиданные точки
        # self.rel_matrix_tfidf_processing('name_lemm')
        # self.rel_matrix_tfidf_processing('description_lemm')
        # bad idea too
        # self.rel_matrix_vacancy_processing()
        
        self.update_skill_df()
        self.save_skill_df()

        self.update_prof_df()
        self.save_prof_df()
        
        log.info('Feature procissing completed')


if __name__ == '__main__':

    FeaturesProcessor().process()

    # df = pd.read_csv('data/processed/vacancies.csv', encoding='utf-8')
    # df = fp.update_prof_set(df)
    # df.to_csv('temp/prof_ds.csv', index=False, encoding='utf-8')
