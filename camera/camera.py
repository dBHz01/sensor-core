from multiprocessing import Process, Queue
import cv2
import time

class Camera():
    
    
    def __init__(self, index:int):
        self.index = index
        self.cap = cv2.VideoCapture(self.index)
        self.cap.set(cv2.CAP_PROP_FPS, 60.0)
    
    
    def release(self):
        self.cap.release()
    
    
    def get_new_frame(self):
        return self.cap.read()
    
    