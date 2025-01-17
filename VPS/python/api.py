import errors
import json
import threading
import time
from typing import *

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from gpt_output import *
from logger import Level, Logger
from text_processing import *
from token_manager import TokenManager


class Request(BaseModel):
    language: str
    input: str
    max_length: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None

app = FastAPI()
logger = Logger(__name__)

config = {}
def update_config() -> None:
    global config

    while True:
        try:
            with open("config.json", "r") as f:
                new = json.load(f)
                if config != new:
                    config = new
            
            time.sleep(1)
        except:
            logger.log(Level.WARNING, "Failed to read config file.")

            time.sleep(3)

def create_response(success: bool, error_or_output: Union[str, dict]) -> dict:
    if success:
        return {"success": True, "output": error_or_output}
    return {"success": False, "error": error_or_output.get_dict()}

def generate_output(processed_input: str, parameters: dict, request: Request, index: int, outputs: List[Tuple[int, List[str]]]) -> Tuple[int, List[str]]:
    output = get_output(processed_input, parameters["max_length"], parameters["temperature"], parameters["top_p"])

    if output.startswith("Sorry, the public API is limited to around 20 queries per every 30 minutes."):
        outputs[index] = None
        return

    processed_output = process_output(request.input, output, request.language)

    processed_blocks = process_blocks(processed_output, count_leading_spaces(request.input.splitlines()[-1]), COMMENTS[request.language])
    
    outputs[index] = (score_code("\n".join(processed_blocks), COMMENTS[request.language]), processed_blocks)

@app.post("/generate")
async def generate(request: Request):
    # Validating the parameters and setting them to default values if they're empty
    parameters = {
        "max_length": request.max_length,
        "temperature": request.temperature,
        "top_p": request.top_p
    }

    for parameter in parameters:
        if parameters[parameter] == None:
            parameters[parameter] = config["default_parameters"][parameter]
    
    # Checking if the language is supported
    if request.language not in COMMENTS:
        return create_response(False, errors.LanguageNotSupportedError())
    
    # Checking if the input is not empty
    if len(request.input.strip()) == 0:
        return create_response(False, errors.EmptyInputError())

    # Loggin the request
    logger.log(Level.INFO, {"input": ("... " + request.input.splitlines()[-1].strip()) if len(request.input.splitlines()) > 0 else "...", "max_length": parameters["max_length"], "temperature": parameters["temperature"], "top_p": parameters["top_p"]})

    # Processing the input & output
    processed_input = process_input(request.input, request.language)

    # Generate the outputs and pick the best one
    OUTPUT_COUNT = 2

    # TODO: Rewrite this block of code since it's just really messy
    threads = []
    # outputs = [None for i in range(OUTPUT_COUNT)]
    outputs = []
    [outputs.append(None) for i in range(OUTPUT_COUNT)]
    for i in range(len(outputs)):
        thread = threading.Thread(target=generate_output, args=(processed_input, parameters, request, i, outputs))
        thread.start()
        threads.append(thread)
    for thread in threads: # Wait until all threads are done
        while thread.is_alive():
            time.sleep(0.1)
    
    # Validating the outputs
    for output in outputs:
        if output == None:
            return create_response(False, errors.ApiLimitExceededError())

    # TODO: Use a sorting function instead of this for loop
    max_score, best_output = outputs[0]
    for output in outputs[1:]:
        if output[0] > max_score:
            max_score, best_output = output

    # Return a response
    return create_response(True, best_output)

if __name__ == "__main__":
    # Starting a thread to update the config when it changes
    thread = threading.Thread(target=update_config, daemon=True)
    thread.start()

    # Reading the config file
    with open("config.json", "r") as f:
        config = json.load(f)

    # Creating a token manager
    token_manager = TokenManager(config["token_path"])

    uvicorn.run(app, host=config["host"], port=config["port"], log_level="info")