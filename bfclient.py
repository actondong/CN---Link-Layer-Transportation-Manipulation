#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = 'Acton'
import socket
import sys
import os
import time
import threading
import select
import Queue
import collections
#Name declaration: clnt0 stands for the host which is running current program. clnt1 - clntN will stand for all its neighbors(adjacent hosts).
#
BUFF = 4096
TIMEFORMAT = '%Y-%m-%d %X'
TIMEOUT = None
TIMER = None
HOST = None
PORT = None
OUTPOOL_LOCK = threading.Lock()
OUTPOOL = []
#TODO The general architecture

NEIGHBORS_INFO = {
  #For each entry in NEIGHBORS_INFO, key is (IP,Port) tuple and value is the link weight.
  #This table is initialized based on config file given to start
  #the program and will be updated when neighbors disconnect or new neighbors connect
}

NEIGHBORS_TIMER_INFO_LOCK = threading.Lock()
NEIGHBORS_TIMER_INFO = {
 #I will maintain A timer for each neighbor.
 # Whenever ROUTE UPDATE message is received from a neighbor, that neighbor's timestamp is reset.
 #If 3*timeout has passed but without receiving ROUTE UPDATE message from a neighbor,
 # that neighbor will be decided disconnected.
}

DV_INFO_LOCK = threading.Lock()
DV_INFO = {
 #key is (ip, port), value is weight
 #This is the Distance Vector info table, we record shortest 'distance' to each node in the topology for ???each node??? known in the topology
 #This will be updated periodically based on ROUTE UPDATE message or timeout
}

DV_NXT_HOP = {
 # key is (ip,port), value is (ip,port)
 #This is a optimization. Always record next hop to reach each clnt thru shortest parth

}

def pack(ID, hashtable):  # wrap ROUTE UPDATE message into string
    ID_str = ID[0] + ":" + ID[1] #HOST,PORT） --> HOST:PORT
    s = "#" + ID_str + "="
    for key in hashtable:
        key_str = key[0] + ":"+key[1]
        s = s + key_str + "-" + str(hashtable[key]) + ">"
    s.strip()
    return s


def unpack(data):  # unpack the ROUTE UPDATE message
    first_lvl_data_list = data.split("=")
    ID_str = first_lvl_data_list[0][1:]
    ID = tuple(ID_str.split(":"))
    hashtable = {}
    key_value_list = first_lvl_data_list[1].split(">")
    #print key_value_list
    for key_value in key_value_list:
        #print key_value
        if key_value == "":
            continue
        key_value_l = key_value.split("-")
        key_value_l[0] = tuple(key_value_l[0].split(":"))#HOST:PORT ---> (HOST,PORT)
        if key_value_l[1] == "Infinity":
            hashtable[key_value_l[0]] = key_value_l[1]
        else:
            hashtable[key_value_l[0]] = float(key_value_l[1])

    #print pack(ID,hashtable)
    return (ID, hashtable)

def poison_reverse(neighbor,dv_to_send):
    global HOST
    global PORT
    global BUFF
    global DV_INFO
    global DV_NXT_HOP

    ID , hashtable = unpack(dv_to_send)
    for dest in DV_NXT_HOP.keys():
        if dest != neighbor and DV_NXT_HOP[dest] == neighbor:
            hashtable[dest] = 'Infinity'
            poison_dv_to_send = pack(ID,hashtable)
            return poison_dv_to_send
    return dv_to_send

def show_chart():
    global DV_INFO
    global DV_NXT_HOP
    global DV_INFO_LOCK
    DV_INFO_LOCK.acquire()
    for dest in DV_INFO:
        hop = DV_NXT_HOP[dest]
        print "\nDESTINATION :"+str(dest)+" via first hop: "+str(hop)
    DV_INFO_LOCK.release()

#TODO I will use a separate thread to update DV, every time DV needs to be updated under two cases as instructed
#This will finally put a ["#",DV_INFO_COPY] into OUTPOOL which will sit there and wait for sending by sender thread
class DV_Main (threading.Thread):
    global HOST
    global PORT
    global BUFF
    global DV_INFO
    global DV_INFO_LOCK
    pass
    def __init__(self,recv_dv_info):
        threading.Thread.__init__(self)
        self.recv_dv_info = recv_dv_info
    def run(self):
        if self.recv_dv_info[0] == '$':
            pass
        flag = 0#indicates whether dv has been changed or not
        if self.recv_dv_info[0] == '#':
            print "Process ROUTEMSG...in DV_main()"
            #print self.recv_dv_info
            ID, hashtable = unpack(self.recv_dv_info)
            #print hashtable.keys()
            #Up date DV by bf algo.
            if DV_INFO[ID] == "Infinity":
                print "\nA unreachable node now restores"
                DV_INFO_LOCK.acquire()
                DV_INFO[ID] = hashtable[(HOST,PORT)]
                DV_NXT_HOP[ID] = ID
                NEIGHBORS_INFO[ID] = hashtable[(HOST,PORT)]
                DV_INFO_LOCK.release()

            del hashtable[(HOST,PORT)]#this is the link info of a neighbor and 'myself' which is useless because of duplicate

            DV_INFO_LOCK.acquire()
            NEIGHBORS_TIMER_INFO[ID] = time.time()
            for dest in hashtable:
                if dest not in DV_INFO:
                    if hashtable[dest] != "Infinity":#this indicates new non-neighbor node(from perspective of myself) in the topology
                        flag = 1
                        DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                        DV_NXT_HOP[dest] = ID

                else:
                    if hashtable[dest] != "Infinity":
                        if DV_INFO[dest] == "Infinity":
                            flag = 1
                            DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                            DV_NXT_HOP[dest] = ID

                        if DV_INFO[dest] != "Infinity" and DV_INFO[dest] > DV_INFO[ID] + hashtable[dest]:
                            flag = 1
                            DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                            DV_NXT_HOP[dest] = ID
                    if hashtable[dest] == "Infinity":
                        if dest in DV_NXT_HOP and DV_NXT_HOP[dest] == ID:
                            DV_INFO[dest] = "Infinity"
                            DV_NXT_HOP[dest] = None

            DV_INFO_LOCK.release()
        if flag:
            print "UPDATE DV_INFO...in DV_main"
            dv_data_to_send = pack((HOST,PORT),DV_INFO)
            OUTPOOL_LOCK.acquire()
            OUTPOOL.append(dv_data_to_send)
            OUTPOOL_LOCK.release()



#TODO I will use a separate thread Sender to send data(file data or DV data)
class Sender (threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global HOST
        global PORT
        global DV_INFO
        global DV_INFO_LOCK
        global OUTPOOL_LOCK
        socket_send_to_neighbor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sendbuff = BUFF
        socket_send_to_neighbor.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sendbuff)
        while 1:
            OUTPOOL_LOCK.acquire()
            if len(OUTPOOL) > 0:
                info_to_send = OUTPOOL.pop(0)

                if info_to_send[0] == '#':#This is ROUTEUPDATE message
                    for dest in NEIGHBORS_INFO.keys():
                        if NEIGHBORS_INFO[dest] == "Infinity":
                            continue
                        neighbor_host = dest[0]
                        neighbor_port = int(dest[1])
                        info_to_send_poison_reverse = poison_reverse(dest,info_to_send)#apply poison reverse on dv sending to a specific neighbor
                        #print info_to_send_poison_reverse
                        socket_send_to_neighbor.sendto(info_to_send_poison_reverse,(neighbor_host,neighbor_port))
                        print "dv_to_send is sending in Sender()...to "+ neighbor_host + ":"+ str(neighbor_port)
                if info_to_send[0] == '$':
                    pass

            OUTPOOL_LOCK.release()


class Timer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global HOST
        global PORT
        global NEIGHBORS_TIMER_INFO
        global DV_INFO
        global DV_NXT_HOP
        global NEIGHBORS_INFO
        global OUTPOOL
        global OUTPOOL_LOCK
        while 1:
            time.sleep(TIMEOUT)
            heartbeat_dv_update = pack((HOST,PORT),DV_INFO)#In my current design, it is possible to send dv_info many times within TIMEOUT. Here I only ensure at least one update will happen if current host is still alive
            OUTPOOL_LOCK.acquire()
            OUTPOOL.append(heartbeat_dv_update)
            OUTPOOL_LOCK.release()

            check_point = time.time()
            DV_INFO_LOCK.acquire()
            for neighbor,time_last_update in NEIGHBORS_TIMER_INFO.items():
                if NEIGHBORS_INFO[neighbor] == "Infinity":#This is already a unreachable neighbor
                    continue
                if check_point - time_last_update >= 3*TIMEOUT:
                    print "\nA 3*TIMEOUT exception happen" + str(neighbor) + "set to Infinity"
                    DV_INFO[neighbor] = "Infinity"
                    NEIGHBORS_INFO[neighbor] = "Infinity"
                    for dest,first_hop in DV_NXT_HOP.items():
                        if first_hop == neighbor:
                            DV_NXT_HOP[dest] = None
                    dv_info_to_send = pack((HOST,PORT),DV_INFO)
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(dv_info_to_send)
                    OUTPOOL_LOCK.release()
            DV_INFO_LOCK.release()



#TODO I will of course also have a main thread
#main thread
def main(argv):
    global HOST
    global PORT
    global BUFF
    global DV_INFO
    global DV_INFO_LOCK
    global NEIGHBORS_INFO
    global TIMEOUT
    global TIMEFORMAT
    global NEIGHBORS_TIMER_INFO
    global NEIGHBORS_TIMER_INFO_LOCK#this may be not needed

#main thread takes input from both system.input and listening socket through select. However, sending business is under
#the charge of Sender thread.
    if len(argv) != 2:
        sys.exit("clnt asks for config file to start---> ./bfclient clnt_config.txt")

    #TODO process config_file : get ID info of self and all neighbors'
    conf_file = argv[1]
    conf_file_descriptor = open(conf_file, 'r')
    self_host_info = conf_file_descriptor.readline().strip()
    self_host_info = self_host_info.split(" ")
    #init listening UDP socket
    HOST = socket.gethostbyname(socket.gethostname())
    PORT = self_host_info[0]
    TIMEOUT = float(self_host_info[1])
    listening_socket =  socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recvbuff = BUFF
    listening_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, recvbuff)
    listening_socket.bind((HOST, int(PORT)))
    print "LISTENING ON" + HOST+":"+PORT
    #init neighbor_host_info
    neighbor_info = conf_file_descriptor.readline().strip()
    DV_INFO_LOCK.acquire()
    while neighbor_info:
        neighbor_info = neighbor_info.split(" ")
        neighbor_ID_info = neighbor_info[0]
        neighbor_ID_tuple = tuple(neighbor_ID_info.split(":"))
        neighbor_weight_info = neighbor_info[1]
        NEIGHBORS_INFO[neighbor_ID_tuple] = float(neighbor_weight_info)
        DV_INFO[neighbor_ID_tuple] = float(neighbor_weight_info)
        DV_NXT_HOP[neighbor_ID_tuple] = neighbor_ID_tuple
        NEIGHBORS_TIMER_INFO[neighbor_ID_tuple] = time.time()
        neighbor_info = conf_file_descriptor.readline().strip()
    DV_INFO_LOCK.release()
    dv_to_send = pack((HOST,PORT),DV_INFO)#Pack DV_INFO, ready to be sent to neighbors
    OUTPOOL_LOCK.acquire()
    OUTPOOL.append(dv_to_send)
    OUTPOOL_LOCK.release()
    #Notice actually I only need READ_LIST in this thread
    READ_LIST = []
    WRITE_LIST = []
    EXCEPTION_LIST = []
    READ_LIST.append(listening_socket)
    READ_LIST.append(sys.stdin)
    sender_thread = Sender()
    sender_thread.setDaemon(True)
    sender_thread.start()
    timer_thread = Timer()
    timer_thread.setDaemon(True)
    timer_thread.start()
    while 1:
        readable_list, writable_list, exceptionable_list = select.select(READ_LIST, WRITE_LIST,EXCEPTION_LIST)
        for fd in readable_list:
            if fd == sys.stdin:
                command = (raw_input("[Issue your command(to see what commands are supported, type help)]:")).strip()
                command_list = command.split(" ")
                command_label = command_list[0]
                if command_label == "LINKDOWN" or "LINKUP" or "CHANGECOST":
                    command_info = "$"+" "+command
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(command_info)#this command info requires updating on both sides of the link modified
                    OUTPOOL_LOCK.release()

                    dv_thread = DV_Main(command_info)
                    dv_thread.setDaemon(True)
                    dv_thread.run()
                if command_label == "CLOSE":
                     pass
                if command_label == "SHOWRT":
                     show_chart()

            else:
                print "Info is coming thru UDP socket.."
                recv_data,addr = fd.recvfrom(BUFF)
                if recv_data[0] == '#':#this is ROUTE_UPDATE msg
                    print "I got ROUTEMSG from neighbors!"
                    dv_thread = DV_Main(recv_data)
                    dv_thread.setDaemon(True)
                    dv_thread.run()
                elif recv_data[0] == '$':#this is commands msg which include LINKDOWN,LINKUP,CLOSE
                    pass
                else:
                    pass


if __name__ == '__main__':

        main(sys.argv)




